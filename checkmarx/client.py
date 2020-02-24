from ci.util import urljoin
import model.checkmarx
import requests
from dacite import from_dict
import datetime
from .model import ScanResult, AuthResponse, ScanResponse, ProjectDetails, ScanSettings
import time
import dataclasses


def require_auth(f: callable):
    def wrapper(checkmarx_client: 'CheckmarxClient', *args, **kwargs):
        checkmarx_client._auth()
        res = f(checkmarx_client, *args, **kwargs)
        return res

    return wrapper


class CXNotOkayException(Exception):
    def __init__(self, res: requests.Response, msg: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.res = res
        self.msg = msg

    def __repr__(self):
        return f'CXNotOkayException: {self.msg}'


class CheckmarxRoutes:
    '''Checkmarx REST API endpoints for the checkmarx base URL.
    '''

    def __init__(self, base_url: str):
        self.base_url = base_url

    def _api_url(self, *parts, **kwargs):
        return urljoin(self.base_url, 'cxrestapi', *parts)

    def auth(self):
        return self._api_url('auth', 'identity', 'connect', 'token')

    def projects(self):
        return self._api_url('projects')

    def project_by_id(self, project_id: int):
        return urljoin(self.projects(), str(project_id))

    def scan(self):
        return self._api_url('sast', 'scans')

    def scan_by_id(self, scan_id: str):
        return urljoin(self.scan(), str(scan_id))

    def upload_zipped_source(self, project_id: int):
        return urljoin(str(self.project_by_id(project_id)), 'sourceCode', 'attachments')

    def remote_settings_git(self, project_id: str):
        return urljoin(self.scan_by_id(project_id), 'sourceCode', 'remoteSettings', 'git')


class CheckmarxClient:
    def __init__(self, checkmarx_cfg: model.checkmarx.CheckmarxConfig):
        self.routes = CheckmarxRoutes(base_url=checkmarx_cfg.base_url())
        self.config = checkmarx_cfg
        self.auth = None

    def _auth(self):
        if self.auth and self.auth.is_valid():
            return self.auth

        creds = self.config.credentials()
        res = requests.post(
            self.routes.auth(),
            data={
                'username': creds.qualified_username(),
                'password': creds.passwd(),
                'client_id': creds.client_id(),
                'client_secret': creds.client_secret(),
                'scope': creds.scope(),
                'grant_type': 'password',
            },
            verify=False,
        )
        res = AuthResponse(**res.json())
        res.expires_at = datetime.datetime.fromtimestamp(
            datetime.datetime.now().timestamp() + res.expires_in - 10
        )
        self.auth = res
        return res

    @require_auth
    def request(
            self,
            method: str,
            api_version: str = '1.0',
            print_error: bool = True,
            *args, **kwargs
    ):
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {self.auth.access_token}'
        if 'Accept' not in headers:
            headers['Accept'] = f'application/json;v={api_version}'

        res = requests.request(method=method, verify=False, headers=headers, *args, **kwargs)

        if not res.ok:
            msg = f'{method} request to url {res.url} failed with {res.status_code=} {res.reason=}'
            if print_error:
                print(msg)
                print(res.text)
            raise CXNotOkayException(res=res, msg=msg)
        return res

    def create_project(self, name: str, owning_team: str, is_public: bool):
        res = self.request(
            method='POST',
            url=self.routes.projects(),
            data={
                "name": name,
                "owningTeam": owning_team,
                "isPublic": is_public,
            },
        )
        return res

    def upload_zipped_source_code(self, project_id: int, zipped_source):
        res = self.request(
            method='POST',
            url=self.routes.upload_zipped_source(project_id),
            headers={
                'Accept': 'application/json',
            },
            files={'zippedSource': zipped_source},
        )
        return res

    def update_git_project_remote_settings(self, project_id: str, url: str, branch_ref: str):
        res = self.request(
            method='POST',
            url=self.routes.remote_settings_git(project_id),
            data={
                "url": url,
                "branch": branch_ref,
            },
        )
        return res

    def get_project_id_by_name(self, project_name: str, team_id: str):
        res = self.request(
            method='GET',
            url=self.routes.projects(),
            params={
                'projectName': project_name,
                'teamId': team_id,
            },
            print_error=False,
        )
        return res.json()[0].get('id')

    def get_project_by_id(self, project_id: int):
        res = self.request(
            method='GET',
            url=self.routes.project_by_id(project_id=project_id),
        )
        return res

    def update_project(self, project_details: ProjectDetails):
        res = self.request(
            method="PUT",
            url=self.routes.project_by_id(str(project_details.id)),
            data={
                'name': project_details.name,
                'owningTeam': project_details.teamId,
                'customFields': [
                    {
                        'id': 0,
                        'value': 'blubb'
                    }

                    # [dataclasses.asdict(cf) for cf in project_details.customFields]
                ],
            },
        )
        return res

    def start_scan(self, scan_settings: 'ScanSettings'):
        print(dataclasses.asdict(scan_settings))
        res = self.request(
            method='POST',
            url=self.routes.scan(),
            data=dataclasses.asdict(scan_settings),
        )
        return res.json()['id']

    def get_scan_state(self, scan_id: str):
        res = self.request(
            method='GET',
            url=self.routes.scan_by_id(scan_id=scan_id),
        )
        return from_dict(data_class=ScanResponse, data=res.json())

    def wait_for_scan_result(self, scan_id: int, polling_interval_seconds=60):
        def scan_finished():
            result = self.get_scan_state(scan_id=str(scan_id))
            if result.status.id in (ScanResult.FINISHED.value, ScanResult.FAILED.value):
                return result
            return False

        result = scan_finished()
        while not result:
            # keep polling until result is ready
            time.sleep(polling_interval_seconds)
            result = scan_finished()
        return result