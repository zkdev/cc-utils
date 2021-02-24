<%def
  name="draft_release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim">
<%
from makoutil import indent_func
import os
import concourse.steps.component_descriptor_util as cdu
import gci.componentmodel
version_file = job_step.input('version_path') + '/version'
repo = job_variant.main_repository()
draft_release_trait = job_variant.trait('draft_release')
version_operation = draft_release_trait._preprocess()
component_descriptor_v2_path = os.path.join(
    job_step.input('component_descriptor_dir'),
    cdu.component_descriptor_fname(gci.componentmodel.SchemaVersion.V2),
)
ctf_path = os.path.join(
  job_step.input('component_descriptor_dir'),
  product.v2.CTF_OUT_DIR_NAME,
)
%>
import version
import pathlib

import ci.util
import ccc.github
import cnudie.util
import gci.componentmodel as cm

from gitutil import GitHelper
from github.release_notes.util import (
    draft_release_name_for_version,
    ReleaseNotes,
    github_repo_path,
)
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)

if '${version_operation}' != 'finalize':
    raise NotImplementedError(
        "Version-processing other than 'finalize' is not supported for draft release creation"
    )

version_file = ci.util.existing_file(pathlib.Path('${version_file}'))

processed_version = version.process_version(
    version_str=version_file.read_text().strip(),
    operation='${version_operation}',
)

repo_dir = ci.util.existing_dir('${repo.resource_name()}')

have_ctf = os.path.exists('${ctf_path}')
have_cd = os.path.exists('${component_descriptor_v2_path}')
if not have_ctf ^ have_cd:
    ci.util.fail('exactly one of component-descriptor, or ctf-archive must exist')
elif have_cd:
    component_descriptor_v2 = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file('${component_descriptor_v2_path}'),
    )
elif have_ctf:
    component_descriptors = list(cnudie.util.component_descriptors_from_ctf_archive(
        '${ctf_path}',
    ))
    if not component_descriptors:
        ci.util.fail(f'No component descriptor found in CTF archive at ${ctf_path})
    if len(component_descriptors) > 1:
        ci.util.fail(
            f'More than one component descriptor found in CTF archive at ${ctf_path}'
        )
    component_descriptor_v2 = component_descriptors[0]

github_cfg = ccc.github.github_cfg_for_hostname('${repo.repo_hostname()}')

githubrepobranch = GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner='${repo.repo_owner()}',
    repo_name='${repo.repo_name()}',
    branch='${repo.branch()}',
)

github_helper = GitHubRepositoryHelper.from_githubrepobranch(
    githubrepobranch=githubrepobranch,
)

release_notes = ReleaseNotes(
    component=component_descriptor_v2.component,
    repo_dir=repo_dir,
)

release_notes.create(
    start_ref='${repo.branch()}'
)

release_notes_md = release_notes.to_markdown()

draft_name = draft_release_name_for_version(processed_version)
draft_release = github_helper.draft_release_with_name(draft_name)
if not draft_release:
    github_helper.create_draft_release(
        name=draft_name,
        body=release_notes_md,
    )
else:
    if not draft_release.body == release_notes_md:
        draft_release.edit(body=release_notes_md)
    else:
        ci.util.info('draft release notes are already up to date')

ci.util.info("Checking for outdated draft releases to delete")
for release, deletion_successful in github_helper.delete_outdated_draft_releases():
    if deletion_successful:
        ci.util.info(f"Deleted release '{release.name}'")
    else:
        ci.util.warning(f"Could not delete release '{release.name}'")
</%def>
