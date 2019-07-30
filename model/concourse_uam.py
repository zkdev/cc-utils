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

import model.concourse

from model.base import (
    NamedModelElement,
    ModelValidationError,
    BasicCredentials,
)


class ConcourseRole(Enum):
    ANON = 'anon'
    ADMIN = 'admin'
    OWNER = 'owner'
    MEMBER = 'member'
    PIPELINE_OPERATOR = 'pipeline-operator'
    VIEWER = 'viewer'


class AuthType(Enum):
    GITHUB_OAUTH = 'github_oauth'
    LOCAL_USER = 'local_user'


class ConcourseTeam(NamedModelElement):
    def teamname(self):
        return self.name()

    def auths(self):
        return self.raw['auths']

    def roles(self):
        return [
            ConcourseRole(role)
            for role in self.raw.get('roles')
        ]

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'type',
        ]


class AuthFactory(object):
    @staticmethod
    def auth(raw_auth: dict):
        auth_type = AuthType(raw_auth.get('type'))
        if auth_type is AuthType.GITHUB_OAUTH:
            return GithubOAuth(raw_auth.get('name'), raw_auth)
        if auth_type is AuthType.LOCAL_USER:
            return LocalUserAuth(raw_auth.get('name'), raw_auth)


class ConcourseAuth(NamedModelElement):
    def auth_type(self):
        return AuthType(self.raw.get('type'))

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'type',
        ]


class LocalUserAuth(ConcourseAuth, BasicCredentials):
    pass


class GithubOAuth(ConcourseAuth):
    def client_id(self):
        return self.raw.get('client_id')

    def client_secret(self):
        return self.raw.get('client_secret')

    def github_org(self):
        return self.raw.get('github_org')

    def github_team(self):
        return self.raw.get('github_team')

    def github_cfg(self):
        return self.raw.get('github_cfg')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'client_id',
            'client_secret',
            'github_org',
            'github_team',
            'github_cfg',
        ]

class ConcourseUAMConfig(NamedModelElement):
    def all_team_configs(self):
        return [
            ConcourseTeam(team.get('name'), team)
            for team in self.raw.get('teams')
        ]

    def team_configs(self, name: str):
        return [team in self.teams() if team.team_name() == name]

    def main_team_config(self):
        # guaranteed by validation to only contain one entry
        return self.team_configs('main')[0]

    def auths(self):
        return [
            AuthFactory.auth(raw_auth)
            for raw_auth in self.raw.get('auths')
        ]

    def auth(self, name: str):
        for auth in self.auths():
            if auth.name() == name:
                return auth

    def team_auths(
        self,
        team_name: str,
        auth_type: AuthType=None,
        role: ConcourseRole=None,
    ):
        for team_config in self.team_configs(team_name):
            if not role or role in team_config.roles:
                for auth_name in team_config.auths():
                    auth = self.auth(auth_name)
                    if not auth_type or auth_type is auth.auth_type():
                        yield auth

    def validate(self):
        super().validate()
        auth_names = [auth.name() for auth in self.auths]
        # check for duplicates
        for name in auth_names:
            if auth_names.count(name) != 1:
                raise ModelValidationError(
                    f"Concourse UAM config '{self.name()}' has more than one auth with the name "
                    f"'{name}' configured."
                )
        main_team_configs = self.team_configs('main')
        if not main_team_configs:
             raise ModelValidationError(
                f"No configuration for the main team found in Concourse UAM config '{self.name()}'"
            )
        if len(main_team_configs) > 1:
            raise ModelValidationError(
                "More than one configuration for the main team found in "
                f"Concourse UAM config '{self.name()}'"
            )

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'teams',
            'auths',
        ]
