
# KuberDock - is a platform that allows users to run applications using Docker
# container images and create SaaS / PaaS based on these applications.
# Copyright (C) 2017 Cloud Linux INC
#
# This file is part of KuberDock.
#
# KuberDock is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# KuberDock is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KuberDock; if not, see <http://www.gnu.org/licenses/>.

import base64
import json
import os
import pwd
import random
import re
import string
import subprocess
import warnings
from pipes import quote

import click

from .. import exceptions
from ..api_common import (PODAPI_PATH, AUTH_TOKEN_PATH, PSTORAGE_PATH,
                          IMAGES_PATH, PRICING_PATH, POD_CREATE_API_PATH,
                          PREDEFINED_APPS_PATH,
                          REGISTER_PATH)
from ..helper import KubeQuery, PrintOut, echo
from ..image.image import Image

# Some common error messages
ERR_NO_SUCH_ITEM = "No such item"
ERR_INVALID_KUBE_TYPE = "Valid kube type must be set. " \
                        "Run 'kuberdock kube-types' " \
                        "to get available kube types"
ERR_SPECIFY_IMAGE_OPTION = "You must specify an image with option " \
                           "'-C|--container'"


class ResourceCommon(object):
    """Helper class for all resource objects.
    Stores common resource parameters.
    """

    def __init__(self, ctl, printout=False, as_json=False):
        """
        :param ctl: Controller object, must provide 'query' attribute
        :param printout: flag to switch output mode
        :param as_json: flag - perform output as json dump

        """
        self.ctl = ctl
        self.as_json = as_json
        self.printout_flag = printout

    def query(self):
        """Returns query object (KubeQuery)"""
        return self.ctl.query

    def printout(self, data):
        """Prints out data if appropriate flag was set."""
        if self.printout_flag:
            prn = PrintOut(as_json=self.as_json)
            prn.show(data)


class PodResource(object):
    def __init__(self, ctl, printout=False,
                 name=None, filename=None, json=False, **_):
        self.name = name
        self.fin = filename
        self.as_json = json
        self.resource = ResourceCommon(ctl, printout=printout, as_json=json)

    def _find_pod_by_name(self, data):
        # Restore default behaviour on __exit__
        with warnings.catch_warnings():
            # make warnings to raise the exception
            warnings.simplefilter('error', UnicodeWarning)
            for i in data:
                try:
                    if i['name'] == self.name:
                        return i
                except UnicodeWarning:
                    if i['name'].encode('UTF-8') == self.name:
                        return i

    def _get(self):
        query = self.resource.query()
        data = query.unwrap(query.get(PODAPI_PATH))
        if self.name:
            data = self._find_pod_by_name(data)
            if not data:
                raise exceptions.NotApplicable(
                    'Pod "{0}" not found'.format(self.name),
                    as_json=self.as_json)
        return data

    def get(self):
        data = self._get()
        if self.resource.printout_flag:
            self.printout_pods(data)
        return data

    def delete(self):
        pod = self._get()
        if pod is None:
            raise SystemExit('No such item')
        pod_id = str(pod['id'])
        query = self.resource.query()
        query.delete(PODAPI_PATH + pod_id)
        self.resource.printout("Deleted: {0}".format(pod_id))
        self.resource.ctl._set_delayed()

    def create(self):
        yaml_content = self.fin.read()
        if not yaml_content:
            raise SystemExit('Empty file content')
        # API expects yaml file as a string in json structure:
        # {"data": yaml_as_a_string}
        query = self.resource.query()
        answer = query.post(POD_CREATE_API_PATH, {'data': yaml_content})
        if answer and answer.get('status', None) != 'OK':
            raise SystemExit(u'Failed To create pod: {0}'.format(str(answer)))
        data = query.unwrap(answer)
        self.resource.printout(data)
        return data

    @staticmethod
    def _transform(data):
        """ Converts json data of a pod to dict with fields "name", "status",
        "labels", "images".
        We expect here a dict with fields:
            {
              "name": name of the pod,
              "status": status of the pod,
              "labels": dict for some labels, for example {"name": "mypod"},
              "containers": list of containers in the pod, each container is
                 a dict, here we're extracting only "image" field - name
                 of image in the container,
              "template_id": template_id of the pod
              "host": "name of the node
            }
        Name and status will be returned as is, labels will be joined to
        one string. Image names of container also will be joined to one string.

        """
        ready = ['name', 'status', 'template_id', 'host']
        out = dict((k, data.get(k, '???')) for k in ready)
        out['labels'] = u','.join(
            [u'{0}={1}'.format(k, v)
             for k, v in data.get('labels', {}).iteritems()]
        )
        out['images'] = u','.join(
            [i.get('image', 'imageless') for i in data.get('containers', [])]
        )
        return out

    def printout_pods(self, data):
        printout = PrintOut(
            wants_header=True,
            fields=(('name', 32), ('images', 32),
                    ('labels', 64), ('status', 10),
                    ('template_id', 14), ('host', 10)),
            as_json=self.resource.as_json)
        data = data or []
        if not isinstance(data, (list, tuple)):
            data = [data]
        printout.show_list([self._transform(i) for i in data])


class TemplateResource(object):
    def __init__(self, ctl, id=None, filename=None, name=None,
                 origin='unknown',
                 json=False, printout=False, **_):
        self.app_id = id
        self.fin = filename
        self.name = name
        self.origin = origin
        self.as_json = json
        self.resource = ResourceCommon(ctl, printout=printout, as_json=json)

    def get(self):
        query = self.resource.query()
        if not self.app_id:
            raise SystemExit(u'Application id is not specified')
        answer = query.get(PREDEFINED_APPS_PATH + str(self.app_id))
        if answer.get('status', None) != 'OK':
            raise SystemExit(
                u'Application template not found for id = {0}'.format(
                    self.app_id)
            )
        data = query.unwrap(answer)
        self.resource.printout(data)
        return data

    def create(self):
        yaml_content = self.fin.read()
        if not yaml_content:
            raise exceptions.NotApplicable('Empty file content',
                                           as_json=self.as_json)
        query = self.resource.query()
        answer = query.post(PREDEFINED_APPS_PATH, {'template': yaml_content,
                                                   'origin': self.origin,
                                                   'name': self.name})
        if answer.get('status', None) != 'OK':
            raise SystemExit(u'Failed To create pod: {0}'.format(str(answer)))
        data = query.unwrap(answer)
        self.resource.printout(data)
        return data

    def delete(self):
        if not self.app_id:
            raise SystemExit('Empty template identifier')
        query = self.resource.query()
        answer = query.delete(PREDEFINED_APPS_PATH + str(self.app_id))
        if answer.get('status') != 'OK':
            raise SystemExit(u'Failed to delete app template {0}: {1}'.format(
                self.app_id, str(answer)))
        self.resource.printout(
            "Application template has been deleted, id = {0}".format(
                self.app_id)
        )

    def update(self):
        if not self.app_id:
            raise SystemExit('Empty template identifier')
        template_data = {}
        if self.fin is not None:
            template_data['template'] = self.fin.read()
        if self.name is not None:
            template_data['name'] = self.name
        query = self.resource.query()
        answer = query.put(
            PREDEFINED_APPS_PATH + str(self.app_id),
            template_data
        )
        if answer and answer.get('status', None) != 'OK':
            raise SystemExit(u'Failed To update pod: {0}'.format(str(answer)))
        data = query.unwrap(answer)
        self.resource.printout(data)
        return data


class TemplatesResource(object):
    def __init__(self, ctl, page=None, origin=None, printout=False, json=json,
                 **_):
        self.page = page
        self.origin = origin
        self.resource = ResourceCommon(ctl, printout=printout, as_json=json)

    def get(self):
        query = self.resource.query()
        if self.page is None:
            answer = query.get(PREDEFINED_APPS_PATH)
        else:
            answer = query.get(PREDEFINED_APPS_PATH, {"page": self.page})
        if answer.get('status') != 'OK':
            raise SystemExit('Failed to get list of predefined apps templates')
        data = query.unwrap(answer)
        if self.origin is not None:
            data = [i for i in data if
                    i.get('origin', 'unknown') == self.origin]
        self.resource.printout(data)
        return data


RESOURCE_MAP = {
    'pod': PodResource,
    'pods': PodResource,
    'template': TemplateResource,
    'templates': TemplatesResource
}


class KubeCtl(object):
    """
    Class for managing KuberDock entities
    """

    def __init__(self, **args):
        """
        Constructor
        """
        self._args = args
        self.as_json = args.get('json', False)
        self.query = KubeQuery(jsonify_errors=self.as_json, **args)
        for key, val in args.iteritems():
            setattr(self, key, val)

    def _get_pod(self):
        data = self.query.unwrap(self.query.get(PODAPI_PATH))

        # Restore default behaviour on __exit__
        with warnings.catch_warnings():
            # make warnings to raise the exception
            warnings.simplefilter('error', UnicodeWarning)
            for i in data:
                try:
                    if i['name'] == self.name:
                        return i
                except UnicodeWarning:
                    if i['name'].encode('UTF-8') == self.name:
                        return i

    @echo
    def register(self):
        res = self.query.post(REGISTER_PATH, '', True)
        if res.get('status').lower() != 'ok':
            raise SystemExit(str(res))

    def get(self):
        """
        Gets a list of user pods and prints either all or one
        """
        resource = self._get_resource(True)
        if not resource:
            raise SystemExit('Unknown resource')
        resource.get()

    def describe(self):
        """
        Gets a list of user pods, filter out one of them by name and prints it
        """
        resource = self._get_resource(False)
        if not resource:
            raise SystemExit('Unknown resource')
        data = resource.get()
        if data:
            printout = PrintOut(as_json=self.as_json)
            printout.show(data)
        else:
            raise SystemExit(ERR_NO_SUCH_ITEM)

    @echo
    def delete(self):
        """
        Gets a list of user pods,
        filter out one of them by name and deletes it.
        """
        resource = self._get_resource(True)
        if not resource:
            raise SystemExit('Unknown resource')
        resource.delete()

    @echo
    def create(self):
        """Creates resource"""
        resource = self._get_resource(True)
        if not resource:
            raise SystemExit('Unknown resource')
        resource.create()

    @echo
    def update(self):
        """Updates resource"""
        resource = self._get_resource(True)
        if not resource:
            raise SystemExit('Unknown resource')
        resource.update()

    def postprocess(self):
        if os.geteuid() != 0:
            raise SystemExit('The postprocess expects superuser privileges')
        if not hasattr(self, 'uid'):
            raise SystemExit('User UID is expected')

        data = self.query.unwrap(self.query.get(PODAPI_PATH))
        pod = [i for i in data if i['name'] == self.name]
        if pod:
            service_ip = pod[0].get('podIP')
            if service_ip is None:
                return
            params = ['/sbin/iptables', '-t', 'nat', '-C', 'OUTPUT', '-d',
                      service_ip,
                      '-m', 'owner', '!', '--uid-owner', self.uid, '-j',
                      'DNAT',
                      '--to-destination', '233.252.0.254']
            try:
                subprocess.check_call(params, stdout=open('/dev/null', 'a'),
                                      stderr=open('/dev/null', 'a'))
            except subprocess.CalledProcessError:
                params[3] = '-I'
                subprocess.call(params, stdout=open('/dev/null', 'a'),
                                stderr=open('/dev/null', 'a'))
        else:
            existing_ips = [i['podIP'] for i in data if 'podIP' in i]
            params = ['/sbin/iptables', '-t', 'nat', '-L', 'OUTPUT', '-n',
                      '--line-numbers']
            rv = subprocess.Popen(params, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            o, e = rv.communicate()
            rules = o.splitlines()
            patt = re.compile(r'!\sowner\sUID\smatch\s(?P<uid>\d+)')
            for i in reversed(rules[2:]):
                fields = i.split(None, 6)
                m = patt.match(fields[6])
                if m is None:
                    continue
                if m.group('uid') != self.uid:
                    continue
                if fields[5] not in existing_ips:
                    params = ['/sbin/iptables', '-t', 'nat', '-D', 'OUTPUT',
                              fields[0]]
                    try:
                        subprocess.check_call(params,
                                              stdout=open('/dev/null', 'a'),
                                              stderr=open('/dev/null', 'a'))
                    except subprocess.CalledProcessError:
                        click.echo("Could not delete rule for uid {0} ({1})"
                                   .format(self.uid, fields[5]))

    def _get_resource(self, printout):
        resource_cls = RESOURCE_MAP.get(self._args['resource'])
        if not resource_cls:
            return None
        return resource_cls(self, printout=printout, **self._args)

    def _set_delayed(self):
        """Delayed change of iptables rules (postprocess method).
        After deleting/creation of pod we should change iptables rules, but
        we don't know if the operation actually have been performed. So, wait
        for 2 minutes and call postprocess method as superuser (via suid
        binary 'suidwrap').

        """
        token = getattr(self, 'token', None)
        if not token or token == 'None':
            data = self.query.get(AUTH_TOKEN_PATH)
            token = data['token']
        try:
            fmt = 'echo /usr/libexec/suidwrap "{0}" {1} ' \
                  '|at now + 2 minute > /dev/null 2>&1'
            subprocess.check_call([fmt.format(token, quote(self.name))],
                                  shell=True)
        except (KeyError, TypeError, subprocess.CalledProcessError):
            return


class KuberDock(KubeCtl):
    """
    Class for creating KuberDock entities
    """
    # default directory for storing container configs
    KUBEDIR = '.kube_containers'
    # file extension to store container config
    EXT = '.kube'

    def __init__(self, **args):
        """Creates empty parameters for container configuration.
        TODO: separate field sets of cli parameters and container configuration
        """
        # First we need to load possibly saved configuration for a new pod
        # and only after loading apply data
        self.containers = []
        self.volumes = []
        # Container configs path
        self._kube_path = None
        # pending container path
        self._data_path = None
        # Need to set resource type from KubeCtl
        args['resource'] = 'pod'
        self._initialized = self._load(args)
        super(KuberDock, self).__init__(**args)

    def create(self):
        self.set()

    @echo
    def set(self):
        """Creates or updates temporary pod configuration on the local host"""
        if hasattr(self, 'image'):
            i = self._get_image()
            i.data['kubes'] = int(self.kubes)

        if getattr(self, 'list_env', False):
            return self._list_env()

        if self.delete is None:
            self._save()
        else:
            self._delete_container_image()
            self._save()

    @echo
    def save(self):
        """
        Sends POST request to KuberDock to save configured container
        """
        if not self._initialized:
            raise exceptions.NotApplicable(
                """Pod data missing or contains garbage.
                Try running "kcli kuberdock forget"
                then "kcli kuberdock create" to recreate pod to be created""",
                as_json=self.as_json)
        data = self._prepare(final=True)
        kube_types = self._get_kube_types()
        try:
            data['kube_type'] = int(kube_types[data['kube_type']])
        except (KeyError, ValueError, TypeError):
            raise SystemExit(ERR_INVALID_KUBE_TYPE)
        try:
            res = self.query.post(PODAPI_PATH, json.dumps(data), True)
            if res.get('status').lower() == 'ok':
                self._clear()
            else:
                raise SystemExit(str(res))
        except TypeError, e:
            raise SystemExit(str(e))

    @echo
    def list(self):
        """
        Lists all pending pods
        """
        names = []
        printout = PrintOut(as_json=self.as_json)
        try:
            for f in os.listdir(self._kube_path):
                if not f.endswith(self.EXT):
                    continue
                names.append(f[:f.index(self.EXT)])
        except OSError:
            pass

        printout.show_list([{'name': base64.b64decode(i)} for i in names])

    def kube_types(self):
        """
        Return list of available kube types
        """
        printout = PrintOut(wants_header=True,
                            fields=(('id', 12), ('name', 32)),
                            as_json=self.as_json)
        data = [{'name': k, 'id': v} for k, v in
                self._get_kube_types().iteritems()]
        data.sort()
        printout.show_list(data)

    def drives(self):
        """
        Persistent drives related actions
        """
        {'list': self.list_drives,
         'add': self.add_drive,
         'delete': self.delete_drive}.get(self.pdaction, self.list_drives)()

    @echo
    def list_drives(self):
        """
        Returns list of user persistent drives
        """
        printout = PrintOut(
            wants_header=True,
            fields=(('id', 48), ('name', 32), ('size', 12), ('in_use', 12)),
            as_json=self.as_json
        )
        printout.show_list(self._get_drives())

    @echo
    def add_drive(self):
        """
        Creates a persistent drive for a user
        """
        self.query.post(PSTORAGE_PATH, {'name': self.name, 'size': self.size})

    @echo
    def delete_drive(self):
        """
        Deletes a user persistent drive
        """
        drives = self._get_drives()
        filtered = [d for d in drives if d.get('name') == self.name]
        if not filtered:
            raise SystemExit('No such drive')
        self.query.delete(PSTORAGE_PATH + filtered[0]['id'])

    @echo
    def start(self):
        """Starts a pod with specified name"""
        printout = PrintOut(
            fields=(('status', 32),),
            as_json=self.as_json
        )
        pod = self._get_pod()
        if not pod:
            raise exceptions.NotApplicable(
                'Pod "{0}" not found'.format(self.name),
                as_json=self.as_json)
        if pod['status'] != 'stopped':
            raise exceptions.NotApplicable(
                'Pod {0} has already been started.'.format(self.name),
                as_json=self.as_json)
        command = {'command': 'start'}
        res = self.query.unwrap(
            self.query.put(PODAPI_PATH + pod['id'], command))
        printout.show(res)
        self._set_delayed()

    @echo
    def stop(self):
        """Stops a pod with specified name"""
        printout = PrintOut(
            fields=(('status', 32),),
            as_json=self.as_json
        )
        pod = self._get_pod()
        if pod['status'] not in ['running', 'pending']:
            raise exceptions.NotApplicable(
                'Pod {0} has already been stopped.'.format(self.name),
                as_json=self.as_json)
        command = {'command': 'stop'}
        res = self.query.unwrap(
            self.query.put(PODAPI_PATH + pod['id'], command))
        printout.show_list(res)

    @echo
    def forget(self):
        """
        Deletes one or all pending containers
        """
        if self.name:
            return self._forget_one()
        return self._forget_all()

    @echo
    def search(self):
        """Searches for images with specified name. Optionally there may be
        defined url for a registry where the search should be performed.
        """
        image = Image(vars(self), **self._args)
        image.search()

    def image_info(self):
        """Prints out information about image specified in 'image' parameter"""
        image = Image(vars(self), **self._args)
        image.get()

    @echo
    def describe(self):
        """Describes pending pod."""
        if not os.path.exists(self._data_path):
            raise SystemExit(ERR_NO_SUCH_ITEM)
        printout = PrintOut(as_json=self.as_json, fields=None)
        data = self._prepare()
        printout.show(data)

    def _forget_all(self):
        """
        Deletes all pending containers
        """
        try:
            for f in os.listdir(self._kube_path):
                if not f.endswith(self.EXT):
                    continue
                _path = os.path.join(self._kube_path, f)
                os.unlink(_path)
        except OSError:
            pass

    def _forget_one(self):
        """
        Deletes a given pending container
        """
        if self._data_path:
            if not os.path.isfile(self._data_path):
                raise SystemExit(
                    "Temporary pod {} isn't found".format(self.name))
            os.unlink(self._data_path)

    def _load(self, args):
        """
        Loads prevously saved pod data from a json file
        :param args: dict -> command line arguments
        """
        name = args.get('name', 'unnamed-1')
        self.config = args.get('config', None)
        self._resolve_data_path(name)
        try:
            with open(self._data_path) as data:
                for attr, val in json.load(data).items():
                    setattr(self, attr, val)
        except (IOError, ValueError, TypeError):  # no file, no JSON
            return False
        return True

    def _list_env(self):
        if not hasattr(self, 'image'):
            raise exceptions.NotApplicable('To show envvars image is expected',
                                           as_json=self.as_json)
        po = PrintOut(as_json=self.as_json,
                      fields=(('name', 32), ('value', 48)))
        for c in self.containers:
            if c.get('image') == self.image:
                po.show_list(c.get('env', []))

    def _save(self):
        """
        Saves current container as JSON file
        """
        if self._data_path is None:
            raise SystemExit("No data path. No place to save to")

        # Trying to create the folder for storing configs.
        try:
            os.mkdir(self._kube_path)
        except OSError, e:
            if e.strerror != 'File exists':
                raise SystemExit(e.strerror)
        if not os.path.exists(self._data_path) and self.action == 'set':
            raise SystemExit("Use create command before setup pod")

        with open(self._data_path, 'w') as o:
            json.dump(self._prepare(), o)

    def _prepare(self, final=False):
        valid = set([
            'name', 'containers', 'volumes', 'service',
            'replicas', 'kube_type', 'restartPolicy', 'public_ip',
        ])
        self._prepare_volumes(final)
        self._prepare_ports()
        self._prepare_env()
        data = dict((key, value) for key, value in vars(self).iteritems()
                    if key in valid)

        return data

    def _prepare_volumes(self, final=False):
        """
        Makes names for volumeMount entries and populate 'volumes' with them
        Prepares 'volumeMounts' fields in items of self.containers list,
        also prepares self.volumes list.
        """
        for c in self.containers:
            if c.get('volumeMounts') is None:
                c['volumeMounts'] = []
                continue

            if final:  # We cannot send volumeMount if has no match in volumes
                c['volumeMounts'] = [v for v in c['volumeMounts']
                                     if v.get('mountPath') and v.get('name')]
                continue

            c['volumeMounts'] = [v for v in c['volumeMounts']
                                 if v.get('mountPath')]

            if hasattr(self, 'persistent_drive'):
                if getattr(self, 'image', None) != c['image']:
                    continue

                if not hasattr(self, 'mount_path'):
                    raise SystemExit('"--mount-path" option is expected')
                elif not re.match('^[\w/.-]*$', self.mount_path):
                    raise SystemExit('"--mount-path" should contain letters '
                                     'of Latin alphabet or "/", "_", "-" '
                                     'symbols')
                elif len(self.mount_path) > 30:
                    raise SystemExit('"--mount-path" maximum length '
                                     'is 30 symbols.')

                curr = filter((lambda i: i['name'] == self.persistent_drive),
                              self._get_drives())
                if not curr and not hasattr(self, 'size'):
                    raise SystemExit(
                        'Drive not found. To set a new drive option '
                        '"--size" is expected')

                mount_paths = [i for i in c['volumeMounts']
                               if i['mountPath'] == self.mount_path]
                if mount_paths:
                    mount_path = mount_paths[0]
                else:
                    mount_path = {'mountPath': self.mount_path}
                    c['volumeMounts'].append(mount_path)

                if not mount_path.get('name'):
                    mount_path['name'] = self._generate_image_name(
                        self.mount_path.lstrip('/').replace('/', '-').lower())

                vols = [v for v in self.volumes
                        if v.get('name') == mount_path['name']]
                if vols:
                    vol = vols[0]
                else:
                    vol = {'name': mount_path['name']}
                    self.volumes.append(vol)
                vol['persistentDisk'] = {
                    'pdName': self.persistent_drive,
                    'pdSize': getattr(self, 'size', None)}

    def _prepare_ports(self):
        """Checks if all necessary port entry data are set"""
        if not hasattr(self, 'container_port'):
            return
        if not hasattr(self, 'image'):
            # 'image' is defined by --container option
            raise SystemExit(ERR_SPECIFY_IMAGE_OPTION)

        # We are expecting here
        # something like +1234:567:tcp
        #                ^is public flag (optional)
        #                 ^container port
        #                      ^host port (optional)
        #                          ^protocol (tcp|udp) (optional)
        patt = re.compile(
            "^(?P<public>\+)"
            "?(?P<container_port>\d+)\:"
            "?(?P<host_port>\d+)?\:?"
            "(?P<protocol>tcp|udp)?$"
        )
        ports = []

        min_port = 1
        max_port = 2 ** 16

        right_format_error_message = \
            "Wrong port format. " \
            "Example: +453:54:udp where '+' is a public IP, " \
            "453 - container port, 54 - pod port, " \
            "'udp' - protocol (tcp or udp)"

        for p in getattr(self, 'container_port').strip().split(','):
            m = patt.match(p)
            if m:
                public = bool(m.group('public'))
                container_port = int(m.group('container_port'))
                host_port = m.group('host_port')
                host_port = int(host_port) if host_port else container_port
                protocol = m.group('protocol') if m.group(
                    'protocol') else 'tcp'
                if any([container_port < min_port,
                        container_port >= max_port,
                        host_port < min_port,
                        host_port >= max_port,
                        protocol not in ('tcp', 'udp')]):
                    raise SystemExit(right_format_error_message)

                ports.append({
                    'isPublic': public,
                    'containerPort': container_port,
                    'hostPort': host_port,
                    'protocol': protocol,
                })
            else:
                raise SystemExit(right_format_error_message)

        for c in self.containers:
            if c['image'] != self.image:
                continue
            c['ports'] = ports

    def _prepare_env(self):
        """
        Adds, modifies or deletes container environment variables
        """
        if not hasattr(self, 'env') and not hasattr(self, 'delete_env'):
            return
        if not hasattr(self, 'image'):
            raise SystemExit(ERR_SPECIFY_IMAGE_OPTION)
        for container in self.containers:
            if container['image'] != self.image:
                continue
            if 'env' not in container:
                container['env'] = []
            if hasattr(self, 'env'):
                self._add_or_update_env(container)
            if hasattr(self, 'delete_env'):
                self._delete_env(container)

    def _delete_env(self, container):
        """Deletes environment variables"""
        container['env'] = [i for i in container['env']
                            if i['name'] not in self.delete_env.split(',')]

    def _add_or_update_env(self, container):
        """Adds or modifies environment variables"""
        existing = set(item['name'] for item in container['env'])
        data_to_add = [dict(zip(['name', 'value'], item.strip().split(':')))
                       for item in self.env.strip().split(',')
                       if len(item.split(':')) == 2]
        for i in container['env']:
            for j in data_to_add:
                if i['name'] == j['name']:
                    i['value'] = j['value']
                    break
        container['env'].extend(
            filter((lambda x: x['name'] not in existing), data_to_add))

    def _resolve_containers_directory(self):
        """
        Container configs are kept in a user homedir. Get the path to it
        """
        if self._kube_path is not None:
            return

        if hasattr(self, 'config') and self.config:
            homedir = os.path.dirname(os.path.expanduser(self.config))
        else:
            uid = os.geteuid()
            homedir = pwd.getpwuid(uid).pw_dir

        self._kube_path = os.path.join(homedir, self.KUBEDIR)

    def _resolve_data_path(self, name):
        """
        Get the path of a pending container config
        :param name: string -> name of pening pod
        """
        if self._data_path is not None:
            return
        self._resolve_containers_directory()
        encoded_name = base64.urlsafe_b64encode(name) + self.EXT
        self._data_path = os.path.join(self._kube_path, encoded_name)

    def _get_image(self):
        """
        Return image data from a previously saved image or create a new one
        and populate it with pulled data
        :param name: image name, i.e fedora/apache -- string
        """
        for item in self.containers:
            if item.get('image') == self.image:
                return Image(item,
                             **self._args)  # return once configured image

        _n = self._generate_image_name(self.image)  # new image
        image = {'image': self.image, 'name': _n}
        try:
            pulled = self.query.unwrap(
                self.query.post(IMAGES_PATH + 'new', {'image': self.image}))
        except (AttributeError, TypeError):
            pulled = {}

        if 'volumeMounts' in pulled:
            pulled['volumeMounts'] = [{'mountPath': x}
                                      for x in pulled['volumeMounts']]
        if 'ports' in pulled:
            pulled['ports'] = [
                {
                    'isPublic': False,
                    'containerPort': x.get('number'),
                    'hostPort': x.get('number'),
                    'protocol': x.get('protocol')
                } for x in pulled['ports']]
        image.update(pulled)
        self.containers.append(image)
        return Image(image, **self._args)

    @staticmethod
    def _generate_image_name(name, length=10):
        random_sample = ''.join(random.sample(string.digits, length))
        try:
            return name[name.index('/') + 1:] + random_sample
        except ValueError:
            return name + random_sample

    def _get_kube_types(self):
        """
        Get available kube types from backend
        """
        return self.query.unwrap(self.query.get(PRICING_PATH + 'userpackage'))

    def _get_drives(self):
        """
        Gets user drives info from backend
        """
        return self.query.unwrap(self.query.get(PSTORAGE_PATH))

    def _clear(self):
        """Deletes pending pod file"""
        os.unlink(self._data_path)

    def _delete_container_image(self):
        self.containers = [c for c in self.containers if
                           c['image'] != self.delete]
