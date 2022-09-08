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

import collections
import dataclasses
import dso.labels
import enum
import tarfile
import tarutil
import traceback
import typing

import dacite

import gci.componentmodel as cm

import ccc
import ccc.oci
import gci.componentmodel
import github.compliance.model as gcm
import oci

from concourse.model.base import (
    AttribSpecMixin,
    AttributeSpec,
)
from model.base import ModelBase


class VersionOverrideScope(enum.Enum):
    APP = 1
    GROUP = 2
    GLOBAL = 3


class ProcessingStatus(enum.Enum):
    BUSY = 'B'
    READY = 'R'
    FAILED = 'F'


class CVSSVersion(enum.Enum):
    V2 = 'CVSSv2'
    V3 = 'CVSSv3'


class Product(ModelBase):
    def product_id(self) -> int:
        return self.raw['product_id']

    def custom_data(self) -> typing.Dict[str, str]:
        return self.raw.get('custom_data', dict())

    def name(self) -> str:
        return self.raw['name']


class AnalysisResult(ModelBase):
    def product_id(self) -> int:
        return self.raw.get('product_id')

    def report_url(self):
        return self.raw.get('report_url')

    def display_name(self) -> str:
        return self.raw.get('filename', '<None>')

    def name(self):
        return self.raw.get('name')

    def status(self) -> ProcessingStatus:
        return ProcessingStatus(self.raw.get('status'))

    def components(self) -> 'typing.Generator[Component, None, None]':
        return (Component(raw_dict=raw) for raw in self.raw.get('components', []))

    def custom_data(self) -> dict[str, str]:
        return self.raw.get('custom_data')

    def greatest_cve_score(self) -> float:
        greatest_cve_score = -1

        for component in self.components():
            greatest_cve_score = max(component.greatest_cve_score(), greatest_cve_score)

        return greatest_cve_score

    def is_stale(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the stored scan result
        has become "stale" (meaning that a rescan would potentially return different
        results).
        '''
        return self.raw.get('stale')

    def has_binary(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the uploaded file is still present.
        In case the uploaded file is no longer present, it needs to be re-uploaded prior to
        rescanning.
        '''
        return self.raw.get('rescan-possible')

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.display_name()}({self.product_id()})'


class Component(ModelBase):
    def name(self) -> str:
        return self.raw.get('lib')

    def version(self) -> str:
        return self.raw.get('version')

    def vulnerabilities(self) -> 'typing.Generator[Vulnerability,None, None]':
        return (Vulnerability(raw_dict=raw) for raw in self.raw.get('vulns'))

    def greatest_cve_score(self) -> float:
        greatest_cve_score = -1

        for vulnerability in self.vulnerabilities():
            if vulnerability.historical() or vulnerability.has_triage():
                continue

            greatest_cve_score = max(vulnerability.cve_severity(), greatest_cve_score)

        return greatest_cve_score

    def license(self) -> 'License':
        license_raw = self.raw.get('license', None)
        if not license_raw:
            return None
        return License(raw_dict=license_raw)

    def extended_objects(self) -> 'typing.Generator[ExtendedObject, None, None]':
        return (ExtendedObject(raw_dict=raw) for raw in self.raw.get('extended-objects'))

    def __repr__(self):
        return (
            f'{self.__class__.__name__}: {self.name()} '
            f'{self.version() or "Version not detected"}'
        )


class ExtendedObject(ModelBase):
    def name(self):
        return self.raw.get('name')

    def sha1(self):
        return self.raw.get('sha1')


class License(ModelBase):
    def name(self):
        return self.raw.get('name')

    def license_type(self):
        return self.raw.get('type')

    def url(self):
        return self.raw.get('url')

    def __eq__(self, other):
        if not isinstance(other, License):
            return False

        return self.name() == other.name() \
            and self.license_type() == other.license_type() \
            and self.url() == other.url()

    def __hash__(self):
        return hash((
            self.name(),
            self.url(),
            self.license_type(),
        ))


class Vulnerability(ModelBase):
    def historical(self):
        return not self.raw.get('exact')

    def cve(self) -> str:
        return self.raw.get('vuln').get('cve')

    def cve_severity(self, cvss_version=CVSSVersion.V3) -> float:
        if cvss_version is CVSSVersion.V3:
            return float(self.raw.get('vuln').get('cvss3_score'))
        elif cvss_version is CVSSVersion.V2:
            return float(self.raw.get('vuln').get('cvss'))
        else:
            raise NotImplementedError(f'{cvss_version} not supported')

    def cve_severity_str(self, cvss_version=CVSSVersion.V3):
        return str(self.cve_severity(cvss_version=cvss_version))

    def has_triage(self) -> bool:
        return bool(self.raw.get('triage')) or bool(self.raw.get('triages'))

    def triages(self) -> 'typing.Generator[Triage, None, None]':
        if not self.has_triage():
            return ()
        trs = self.raw.get('triage')
        if not trs:
            trs = self.raw.get('triages')

        return (Triage(raw_dict=raw) for raw in trs)

    def cve_major_severity(self, cvss_version) -> float:
        if self.cve_severity_str(cvss_version):
            return float(self.cve_severity_str(cvss_version))
        else:
            return -1

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.cve()}'


class TriageScope(enum.Enum):
    ACCOUNT_WIDE = 'CA'
    FILE_NAME = 'FN'
    FILE_HASH = 'FH'
    RESULT = 'R'
    GROUP = 'G'


class Triage(ModelBase):
    def id(self):
        return self.raw['id']

    def vulnerability_id(self):
        return self.raw['vuln_id']

    def component_name(self):
        return self.raw['component']

    def component_version(self):
        return self.raw['version']

    def scope(self) -> TriageScope:
        return TriageScope(self.raw['scope'])

    def reason(self):
        return self.raw['reason']

    def description(self):
        return self.raw.get('description')

    def applies_to_same_vulnerability_as(self, other) -> bool:
        if not isinstance(other, Triage):
            return False
        return self.vulnerability_id() == other.vulnerability_id()

    def __repr__(self):
        return (
            f'{self.__class__.__name__}: {self.id()} '
            f'({self.component_name()} {self.component_version()}, '
            f'{self.vulnerability_id()}, Scope: {self.scope().value})'
        )

    def __eq__(self, other):
        if not isinstance(other, Triage):
            return False
        if self.vulnerability_id() != other.vulnerability_id():
            return False
        if self.component_name() != other.component_name():
            return False
        if self.description() != other.description():
            return False
        return True

    def __hash__(self):
        return hash((self.vulnerability_id(), self.component_name(), self.description()))


#############################################################################
## upload result model

class UploadStatus(enum.Enum):
    SKIPPED = 1
    PENDING = 2
    DONE = 4


@dataclasses.dataclass
class BDBA_ScanResult(gcm.ScanResult):
    status: UploadStatus
    result: AnalysisResult

    @property
    def license_names(self) -> typing.Iterable[str]:
        return {l.name() for l in self.licenses}

    @property
    def licenses(self) -> typing.Sequence[License]:
        return [c.license() for c in self.result.components() if c.license()]

    @property
    def greatest_cve_score(self) -> float:
        return self.result.greatest_cve_score()


@dataclasses.dataclass(frozen=True)
class OciResourceBinary:
    artifact: gci.componentmodel.Resource

    def upload_data(self) -> typing.Iterable[bytes]:
        image_reference = self.artifact.access.imageReference
        oci_client = ccc.oci.oci_client()
        yield from oci.image_layers_as_tarfile_generator(
            image_reference=image_reference,
            oci_client=oci_client,
            include_config_blob=False,
        )


@dataclasses.dataclass(frozen=True)
class TarRootfsAggregateResourceBinary:
    artifacts: typing.Iterable[gci.componentmodel.Resource]
    tarfile_retrieval_function: typing.Callable[[gci.componentmodel.Resource], typing.BinaryIO]

    def upload_data(self):
        # For these kinds of binaries, we aggregate all (tar) artifacts that we're given.
        known_tar_sizes = collections.defaultdict(set)

        def process_tarinfo(
            tar_info: tarfile.TarInfo,
        ) -> bool:
            if not tar_info.isfile():
                return True

            file_name = tar_info.name
            if any((size == tar_info.size for size in known_tar_sizes[file_name])):
                # we already have seen a file with the same name and size
                return False

            known_tar_sizes[file_name].add(tar_info.size)
            return True

        tarfiles_and_names = list(
            (
                tarfile.open(fileobj=self.tarfile_retrieval_function(resource), mode='r|*'),
                resource.extraIdentity.get('platform', 'n/a'),
            )
            for resource in self.artifacts
        )

        def tarinfo_callback(
            tarfile_name: str,
        ) -> typing.Callable[[tarfile.TarInfo], tarfile.TarInfo]:
            def callback(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
                if not tarinfo.isfile():
                    return tarinfo
                tarinfo.name = tarfile_name + '/' + tarinfo.name
                return tarinfo
            return callback

        # stream a combined tarfile by omitting the end-of-file marker for all but the last tar-
        # archive we stream. Also, prefix the filenames of the archive we stream so that the
        # origin can be determined when looking at the protecode scan results.
        # Note: This means that a file being prefixed does not necessarily mean that it was _only_
        # included in the tarfile corresponding, but merely that it was present in the _first_
        # file scanned.
        for src_tarfile, tarfile_name in tarfiles_and_names[:-1]:
            yield from tarutil.filtered_tarfile_generator(
                src_tf=src_tarfile,
                filter_func=process_tarinfo,
                tarinfo_callback=tarinfo_callback(tarfile_name=tarfile_name),
                finalise=False,
            )

        final_tarfile, tarfile_name = tarfiles_and_names[-1]
        yield from tarutil.filtered_tarfile_generator(
            src_tf=final_tarfile,
            filter_func=process_tarinfo,
            tarinfo_callback=tarinfo_callback(tarfile_name=tarfile_name),
            finalise=True,
        )


@dataclasses.dataclass(frozen=True)
class ComponentArtifact:
    component: gci.componentmodel.Component
    artifact: gci.componentmodel.Resource


@dataclasses.dataclass
class ScanRequest:
    component: cm.Component
    artefact: cm.Artifact
    # The actual content to be scanned.
    scan_content: typing.Generator[bytes, None, None]
    display_name: str
    target_product_id: int | None
    custom_metadata: dict

    def auto_triage_scan(self) -> bool:
        # hardcode auto-triage to be determined by artefact
        artefact = self.artefact

        # pylint: disable=E1101
        label = artefact.find_label(name=dso.labels.LabelName.BINARY_ID)
        if not label:
            label = artefact.find_label(name=dso.labels.LabelName.BINARY_SCAN)
        if not label:
            return False

        if label.name == dso.labels.LabelName.BINARY_SCAN:
            return True

        scanning_hint = dacite.from_dict(
            data_class=dso.labels.BinaryScanHint,
            data=label.value,
            config=dacite.Config(cast=[dso.labels.ScanPolicy]),
        )
        return scanning_hint.policy is dso.labels.ScanPolicy.SKIP

    def __str__(self):
        return (
            f"ScanRequest(name='{self.display_name}', target_product_id='{self.target_product_id}' "
            f"custom_metadata='{self.custom_metadata}')"
        )


class BdbaScanError(Exception):
    def __init__(
        self,
        scan_request: ScanRequest,
        component: gci.componentmodel.Component,
        artefact: gci.componentmodel.Artifact,
        exception=None,
        *args,
        **kwargs,
    ):
        self.scan_request = scan_request
        self.component = component
        self.artefact = artefact
        self.exception = exception

        super().__init__(*args, **kwargs)

    def print_stacktrace(self):
        c = self.component
        a = self.artefact
        name = f'{c.name}:{c.version}/{a.name}:{a.version}'

        if not self.exception:
            return name + ' - no exception available'

        return name + '\n' + ''.join(traceback.format_tb(self.exception.__traceback__))


@dataclasses.dataclass(frozen=True)
class ArtifactGroup:
    '''
    A set of similar Resources sharing a common declaring Component (not necessarily in the same
    version) and a common logical name.

    Artifact groups are intended to be handled as "virtual Protecode Groups".
    This particularly means they share triages.

    As a very common "special case", a resource group may contain exactly one container image.

    @param component: the Component declaring dependency towards the given images
    @param resources: iterable of Resources; must share logical name
    '''
    name: str
    component_artifacts: typing.MutableSequence[ComponentArtifact] = dataclasses.field(
        default_factory=list
    )

    def component_name(self):
        return self.component_artifacts[0].component.name

    def component_version(self):
        return self.component_artifacts[0].component.version

    def resource_name(self):
        return self.component_artifacts[0].artifact.name

    def resource_type(self):
        return self.component_artifacts[0].artifact.type

    def resource_version(self):
        return self.component_artifacts[0].artifact.version

    @property
    def artefacts(self) -> tuple[gci.componentmodel.Artifact]:
        return tuple((ca.artifact for ca in self.component_artifacts))

    def __str__(self):
        return f'{self.name}[{len(self.component_artifacts)}]'


@dataclasses.dataclass(frozen=True)
class OciArtifactGroup(ArtifactGroup):
    pass


@dataclasses.dataclass(frozen=True)
class TarRootfsArtifactGroup(ArtifactGroup):
    # For these artifact groups, all component versions are the same
    def component_version(self):
        return self.component_artifacts[0].component.version


class ProcessingMode(AttribSpecMixin, enum.Enum):
    RESCAN = 'rescan'
    FORCE_UPLOAD = 'force_upload'

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.RESCAN.value,
                default=None,
                doc='''
                    (re-)scan container images if Protecode indicates this might bear new results.
                    Upload absent images.
                ''',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.FORCE_UPLOAD.value,
                default=None,
                doc='''
                    `always` upload and scan all images.
                ''',
                type=str,
            ),
        )
