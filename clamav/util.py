import concurrent.futures
import functools
import logging
import socket
import tarfile
import traceback
import typing

import requests.exceptions

import ccc.oci
import clamav.client
import gci.componentmodel
import oci.client as oc
import product
import saf.model
import tarutil


logger = logging.getLogger(__name__)


def iter_image_files(
    image_reference: str,
    oci_client: oc.Client=None,
) -> typing.Iterable[typing.Tuple[typing.IO, str]]:
    '''
    returns a generator yielding the regular files contained in the specified oci-image
    as sequence of two-tuples (filelike-obj, <layer-digest:relpath>).

    The image's layer-blobs are retrieve in the order they are defined in the image-manifest.
    cfg-blobs are ignored. All layer-blobs are assued to be tarfiles (which is not necessarily
    a valid assumption for non-docker-compatible oci-artifacts).
    '''
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)

    # we ignore cfg-blob (which would be included in manifest.blobs())
    for layer_blob in manifest.layers:
        blob_resp = oci_client.blob(
            image_reference=image_reference,
            digest=layer_blob.digest,
            stream=True,
        )

        fileobj = tarutil._FilelikeProxy(
            generator=blob_resp.iter_content(
                chunk_size=tarfile.RECORDSIZE,
                decode_unicode=False,
            ),
        )
        with tarfile.open(
            fileobj=fileobj,
            mode='r|*',
        ) as layer_tarfile:
            for tar_info in layer_tarfile:
                if not tar_info.isfile():
                    continue
                yield (
                    layer_tarfile.extractfile(tar_info),
                    f'{layer_blob.digest}:{tar_info.name}',
                )


def _scan_oci_image(
    clamav_client,
    oci_client,
    image_reference: str,
) -> typing.Generator[saf.model.MalwarescanResult, None, None]:
    try:
        content_iterator = iter_image_files(
            image_reference=image_reference,
            oci_client=oci_client,
        )
        findings = clamav_client.scan_container_image(
            content_iterator=content_iterator,
        )
        yield from findings
        return
    except tarfile.TarError as te:
        logger.warning(f'{image_reference=}: {te=} - falling back to layer-scan')

    # fallback to layer-wise scan in case we encounter gzip-uncompression-problems
    def iter_layers():
        manifest = oci_client.manifest(image_reference=image_reference)
        for layer in manifest.layers:
            layer_blob = oci_client.blob(
                image_reference=image_reference,
                digest=layer.digest,
                stream=True,
            )
            yield (layer_blob.iter_content(chunk_size=4096), layer.digest)

    findings = clamav_client.scan_container_image(
        content_iterator=iter_layers(),
    )
    yield from findings


def _try_scan_image(
    oci_resource: gci.componentmodel.Resource,
    clamav_client: clamav.client.ClamAVClient,
    oci_client: oc.Client,
):
    access: gci.componentmodel.OciAccess = oci_resource.access

    try:
        clamav_findings = _scan_oci_image(
            clamav_client=clamav_client,
            oci_client=oci_client,
            image_reference=access.imageReference,
        )

        return saf.model.MalwarescanResult(
                resource=oci_resource,
                scan_state=saf.model.MalwareScanState.FINISHED_SUCCESSFULLY,
                findings=[
                    f'{path}: {scan_result.virus_signature()}'
                    for scan_result, path in clamav_findings
                ],
            )
    except (requests.exceptions.RequestException, socket.gaierror) as e:
        # log warning and include it as finding to document it via the generated report-mails
        warning = f'error while scanning {resource.access.imageReference} {e=}'
        logger.warning(warning)
        traceback.print_exc()

        return saf.model.MalwarescanResult(
                resource=resource,
                scan_state=saf.model.MalwareScanState.FINISHED_WITH_ERRORS,
                findings=[warning],
            )


def virus_scan_images(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    filter_function,
    clamav_client,
    oci_client: oc.Client=None,
) -> typing.Generator[saf.model.MalwarescanResult, None, None]:
    '''Scans components of the given Component Descriptor using ClamAV

    Used by image-scan-trait
    '''
    resources = [
        resource for component, resource
        in product.v2.enumerate_oci_resources(component_descriptor=component_descriptor_v2)
        if filter_function(component, resource)
    ]

    try_scan_func = functools.partial(
        _try_scan_image,
        clamav_client=clamav_client,
        oci_client=oci_client,
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    results = executor.map(
        try_scan_func,
        resources,
    )

    yield from results
