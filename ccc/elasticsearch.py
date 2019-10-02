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

import enum
import datetime
import functools
import os
import json

import elasticsearch

import model.elasticsearch
import concourse.util
import util


class MetadataClass(enum.Flag):
    NONE = enum.auto()
    PIPELINE_METADATA = enum.auto()
    BUILD_METADATA = enum.auto()
    ALL = PIPELINE_METADATA | BUILD_METADATA


def from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    return ElasticSearchClient(
        elasticsearch=_from_cfg(elasticsearch_cfg=elasticsearch_cfg)
    )


def _from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    credentials = elasticsearch_cfg.credentials()
    return elasticsearch.Elasticsearch(
        elasticsearch_cfg.endpoints(),
        http_auth=(credentials.username(), credentials.passwd()),
    )


@functools.lru_cache()
def _pipeline_metadata():
    # XXX mv to concourse package; deduplicate with notify step
    if not util._running_on_ci():
        return {}

    pipeline_metadata = concourse.util.get_pipeline_metadata()
    config_set = util.ctx().cfg_factory().cfg_set(pipeline_metadata.current_config_set_name)
    concourse_cfg = config_set.concourse()

    meta_dict = {
        'build-uuid': pipeline_metadata.build_uuid,
        'build-job-name': pipeline_metadata.job_name,
        'build-team-name': pipeline_metadata.team_name,
        'build-pipeline-name': pipeline_metadata.pipeline_name,
        'atc-external-url': concourse_cfg.external_url(),
    }

    # XXX do not hard-code env variables
    meta_dict['effective_version'] = os.environ.get('EFFECTIVE_VERSION')
    meta_dict['component_name'] = os.environ.get('COMPONENT_NAME')
    meta_dict['creation_date'] = datetime.datetime.now().isoformat()

    return meta_dict


@functools.lru_cache()
def _build_metadata():
    if not util._running_on_ci():
        return {}
    build = concourse.util.find_own_running_build()

    build_metadata_dict = {
        'build-id': build.id(),
        'build-name': str(build.build_number()),
    }

    return build_metadata_dict


def _metadata(metadata_to_inject: MetadataClass):
    md = {}
    if MetadataClass.PIPELINE_METADATA in metadata_to_inject:
        md.update(_pipeline_metadata())
    if MetadataClass.BUILD_METADATA in metadata_to_inject:
        md.update(_build_metadata())
    if (
        MetadataClass.PIPELINE_METADATA in metadata_to_inject and
        MetadataClass.BUILD_METADATA in metadata_to_inject
    ):
        # XXX deduplicate; mv to concourse package
        md['concourse_url'] = util.urljoin(
            md['atc-external-url'],
            'teams',
            md['build-team-name'],
            'pipelines',
            md['build-pipeline-name'],
            'jobs',
            md['build-job-name'],
            'builds',
            md['build-name'],
        )
    return md


class ElasticSearchClient(object):
    def __init__(
        self,
        elasticsearch: elasticsearch.Elasticsearch,
    ):
        self._api = elasticsearch

    def store_document(
        self,
        index: str,
        body: dict,
        inject_metadata=MetadataClass.ALL,
        *args,
        **kwargs,
    ):
        util.check_type(index, str)
        util.check_type(body, dict)
        if 'doc_type' in kwargs:
            raise ValueError(
                '''
                doc_type attribute has been deprecated - see:
                https://www.elastic.co/guide/en/elasticsearch/reference/6.0/removal-of-types.html
                '''
            )

        if inject_metadata and util._running_on_ci():
            body['cc_meta'] = _metadata(metadata_to_inject=inject_metadata)

        return self._api.index(
            index=index,
            doc_type='_doc',
            body=body,
            *args,
            **kwargs,
        )

    def store_documents(
        self,
        index: str,
        body: [dict],
        inject_metadata=MetadataClass.ALL,
        *args,
        **kwargs,
    ):
        # Bulk-loading uses a special format: A json specifying index name and doc-type
        # (always _doc) followed by the actual document json. These pairs (one for each document)
        # are then converted to newline delimited json

        # The index json does not change for bulk-loading into a single index.
        index_json = json.dumps({
            'index': {
                '_index': index,
                '_type': '_doc'
            }
        })
        return self.store_bulk(
            body='\n'.join([f'{index_json}\n{json.dumps(d)}' for d in body]),
            inject_metadata=inject_metadata,
            *args,
            **kwargs,
        )

    def store_bulk(
        self,
        body: str,
        inject_metadata=MetadataClass.ALL,
        *args,
        **kwargs,
    ):
        util.check_type(body, str)

        if inject_metadata and util._running_on_ci():
            md = _metadata(metadata_to_inject=inject_metadata)

            def inject_meta(line):
                parsed = json.loads(line)
                if 'index' not in parsed:
                    parsed['cc_meta'] = md
                    return json.dumps(parsed)
                return line

            patched_body = '\n'.join([inject_meta(line) for line in body.splitlines()])
            body = patched_body

        return self._api.bulk(
            body=body,
            *args,
            **kwargs,
        )
