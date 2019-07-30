# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from enum import Enum

from model.base import (
    NamedModelElement,
    ModelValidationError,
)


class ConcourseApiVersion(Enum):
    '''Enum to define different Concourse versions'''
    V5 = '5'


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def external_url(self):
        return self.raw.get('externalUrl')

    def job_mapping_cfg_name(self):
        return self.raw.get('job_mapping')

    def concourse_uam_config(self):
        return self.raw.get('concourse_uam_config')

    def helm_chart_default_values_config(self):
        return self.raw.get('helm_chart_default_values_config')

    def helm_chart_values(self):
        return self.raw.get('helm_chart_values', None)

    def image_pull_secret(self):
        return self.raw.get('imagePullSecret')

    def tls_secret_name(self):
        return self.raw.get('tls_secret_name')

    def tls_config(self):
        return self.raw.get('tls_config')

    def kubernetes_cluster_config(self):
        return self.raw.get('kubernetes_cluster_config')

    def clamav_config(self):
        return self.raw.get('clamav_config')

    def disable_github_pr_webhooks(self):
        '''
        If set to True, the rendered concourse pull-request resources don't have webhooks configured.
        This is because of problems using webhooks on our internal Github.
        '''
        return self.raw.get('disable_webhook_for_pr', False)

    def ingress_host(self):
        '''
        Returns the hostname added as additional ingress.
        '''
        return self.raw.get('ingress_host')

    def ingress_url(self):
        return 'https://' + self.ingress_host()

    def helm_chart_version(self):
        return self.raw.get('helm_chart_version')

    def concourse_version(self) -> ConcourseApiVersion:
        return ConcourseApiVersion(self.raw.get('concourse_version'))

    def github_enterprise_host(self):
        return self.raw.get('github_enterprise_host')

    def proxy(self):
        return self.raw.get('proxy')

    def monitoring_config(self):
        return self.raw.get('monitoring_config')

    def _required_attributes(self):
        return [
            'externalUrl',
            'concourse_uam_config',
            'helm_chart_default_values_config',
            'kubernetes_cluster_config',
            'concourse_version',
            'job_mapping',
            'imagePullSecret',
            'tls_secret_name',
            'tls_config',
            'ingress_host',
            'helm_chart_version',
            'helm_chart_values',
        ]

    def _optional_attributes(self):
        return {
            'github_enterprise_host',
            'proxy',
            'monitoring_config',
            'clamav_config',
        }

    def validate(self):
        super().validate()
        # Check for valid versions
        if self.concourse_version() not in ConcourseApiVersion:
            raise ModelValidationError(
                'Concourse version {v} not supported'.format(v=self.concourse_version())
            )


class JobMappingSet(NamedModelElement):
    def job_mappings(self):
        return {name: JobMapping(name=name, raw_dict=raw) for name, raw in self.raw.items()}


class JobMapping(NamedModelElement):
    def team_name(self)->str:
        return self.raw.get('concourse_target_team')

    def github_organisations(self):
        return [
            GithubOrganisationConfig(name, raw)
            for name, raw in self.raw.get('github_orgs').items()
        ]

    def _required_attributes(self):
        return ['concourse_target_team']


class GithubOrganisationConfig(NamedModelElement):
    def github_cfg_name(self):
        return self.raw.get('github_cfg')

    def org_name(self):
        return self.raw.get('github_org')
