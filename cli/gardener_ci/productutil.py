# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import itertools
import github3.exceptions

import ccc.protecode
from ci.util import CliHints, CliHint, parse_yaml_file, ctx, fail, info
from product.model import (
    ComponentReference,
    ComponentDescriptor,
)
from product.util import (
    _enumerate_effective_images,
    ComponentDescriptorResolver,
)
from protecode.util import (
    upload_grouped_images,
    ProcessingMode
)
import product.xml


def transport_triages(
    protecode_cfg_name: str,
    from_product_id: int,
    to_group_id: int,
    to_product_ids: [int],
):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    api = ccc.protecode.client(protecode_cfg)

    scan_result_from = api.scan_result(product_id=from_product_id)
    scan_results_to = {
        product_id: api.scan_result(product_id=product_id)
        for product_id in to_product_ids
    }

    def target_component_versions(product_id: int, component_name: str):
        scan_result = scan_results_to[product_id]
        component_versions = {
            c.version() for c
            in scan_result.components()
            if c.name() == component_name
        }
        return component_versions

    def enum_triages():
        for component in scan_result_from.components():
            for vulnerability in component.vulnerabilities():
                for triage in vulnerability.triages():
                    yield component, triage

    triages = list(enum_triages())
    info(f'found {len(triages)} triage(s) to import')

    for to_product_id, component_name_and_triage in itertools.product(to_product_ids, triages):
        component, triage = component_name_and_triage
        for target_component_version in target_component_versions(
            product_id=to_product_id,
            component_name=component.name(),
        ):
            info(f'adding triage for {triage.component_name()}:{target_component_version}')
            api.add_triage(
                triage=triage,
                product_id=to_product_id,
                group_id=to_group_id,
                component_version=target_component_version,
            )
        info(f'added triage for {triage.component_name()} to {to_product_id}')


def upload_grouped_product_images(
    protecode_cfg_name: str,
    product_cfg_file: CliHints.existing_file(),
    processing_mode: CliHint(
        choices=list(ProcessingMode),
        type=ProcessingMode,
    )=ProcessingMode.RESCAN,
    protecode_group_id: int=5,
    parallel_jobs: int=4,
    cve_threshold: int=7,
    ignore_if_triaged: bool=True,
    reference_group_ids: [int]=[],
):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    component_descriptor = ComponentDescriptor.from_dict(
        raw_dict=parse_yaml_file(product_cfg_file)
    )

    upload_results, license_report = upload_grouped_images(
        protecode_cfg=protecode_cfg,
        component_descriptor=component_descriptor,
        protecode_group_id=protecode_group_id,
        parallel_jobs=parallel_jobs,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
        processing_mode=processing_mode,
        reference_group_ids=reference_group_ids,
    )


def component_descriptor_to_xml(
    component_descriptor: CliHints.existing_file(),
    out_file: str,
):
    component_descriptor = ComponentDescriptor.from_dict(parse_yaml_file(component_descriptor))

    image_references = [
        container_image for _, container_image
        in _enumerate_effective_images(component_descriptor=component_descriptor)
    ]

    result_xml = product.xml.container_image_refs_to_xml(
        image_references,
    )

    result_xml.write(out_file)


def retrieve_component_descriptor(
    name: str,
    version: str,
):
    cfg_factory = ctx().cfg_factory()

    resolver = ComponentDescriptorResolver(
        cfg_factory=cfg_factory,
    )

    component_reference = ComponentReference.create(name=name, version=version)
    try:
        resolved_descriptor = resolver.retrieve_raw_descriptor(component_reference)
    except github3.exceptions.NotFoundError:
        fail('no component descriptor found: {n}:{v}'.format(n=name, v=version))

    print(resolved_descriptor)
