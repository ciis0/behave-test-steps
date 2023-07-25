"""
The MIT License (MIT)

Copyright (c) 2015 Red Hat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.mgb
"""
from __future__ import print_function
import docker
import json
import logging
import os
import re
import tarfile
import tempfile
import time

try:
    d = docker.Client(version="1.22")
except:
    d = docker.from_env(version="1.22").api


class ExecException(Exception):
    def __init__(self, message, output=None):
        super(ExecException, self).__init__(message)
        self.output = output


class Container(object):
    """
    Object representing a docker test container, it is used in tests
    """

    def __init__(self, image_id, name=None, remove_image=False, output_dir="output", save_output=True, volumes=None, entrypoint=None, **kwargs):
        self.image_id = image_id
        self.container = None
        self.name = name
        self.ip_address = None
        self.output_dir = output_dir
        self.save_output = save_output
        self.remove_image = remove_image
        self.kwargs = kwargs
        self.logger = logging.getLogger("cekit")
        self.running = False
        self.volumes = volumes
        self.environ = {}
        self.entrypoint = entrypoint

        # get volumes from env (CTF_DOCKER_VOLUME=out:in:z,out2:in2:z)
        try:
            if "CTF_DOCKER_VOLUMES" in os.environ:
                self.volumes = [] if self.volumes is None else None
                self.volumes.extend(os.environ["CTF_DOCKER_VOLUMES"].split(','))
        except Exception as e:
            self.logger.error("Cannot parse CTF_DOCKER_VOLUME variable %s", e)

        # get env from env (CTF_DOCKER_ENV="foo=bar,env=baz")
        try:
            if "CTF_DOCKER_ENV" in os.environ:
                for variable in os.environ["CTF_DOCKER_ENV"].split(','):
                    name, value = variable.split('=', 1)
                    self.environ.update({name: value})
        except Exception as e:
            self.logger.error("Cannot parse CTF_DOCKER_ENV variable, %s", e)

    def __enter__(self):
        self.start(**self.kwargs)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        if self.remove_image:
            self.remove_image()

    def start(self, **kwargs):
        """ Starts a detached container for selected image """
        self._create_container(**kwargs)
        self.logger.debug("Starting container '%s'..." % self.container.get('Id'))
        d.start(container=self.container)
        self.running = True
        self.ip_address = self.inspect()['NetworkSettings']['IPAddress']

    def _remove_container(self, number=1):
        self.logger.info("Removing container '%s', %s try..." % (self.container['Id'], number))
        try:
            d.remove_container(self.container)
            self.logger.info("Container '%s' removed", self.container['Id'])
        except:
            self.logger.info("Removing container '%s' failed" % self.container['Id'])

            if number > 3:
                raise

            # Give 20 more seconds for the devices to cool down
            time.sleep(20)
            self._remove_container(number + 1)

    def stop(self):
        """
        Stops (and removes) selected container.
        Additionally saves the STDOUT output to a `container_output` file for later investigation.
        """
        if self.running and self.save_output:
            if self.name:
                self.name = "%s_%s" % (self.name, self.container.get('Id'))
            else:
                self.name = self.container.get('Id')

            filename = "".join([c for c in self.name if re.match(r'[\w\ ]', c)]).replace(" ", "_")
            out_path = self.output_dir + "/" + filename + ".txt"
            if not os.path.exists(self.output_dir):
                os.makedirs(self.output_dir)
            with open(out_path, 'w') as f:
                print(d.logs(container=self.container.get('Id'), stream=False), file=f)

        if self.container:
            self.logger.debug("Removing container '%s'" % self.container['Id'])
            # Kill only running container
            if self.inspect()['State']['Running']:
                d.kill(container=self.container)
            self.running = False
            self._remove_container()
            self.container = None

    def startWithCommand(self, **kwargs):
        """ Starts a detached container for selected image with a custom command"""
        self._create_container(tty=True, **kwargs)
        self.logger.debug("Starting container '%s'..." % self.container.get('Id'))
        d.start(self.container)
        self.running = True
        self.ip_address = self.inspect()['NetworkSettings']['IPAddress']

    def execute(self, cmd, detach=False):
        """ executes cmd in container and return its output """
        inst = d.exec_create(container=self.container, cmd=cmd)

        if detach:
            d.exec_start(inst, detach)
            return None

        output = d.exec_start(inst, detach=detach)
        retcode = d.exec_inspect(inst)['ExitCode']

        count = 0

        while retcode is None:
            count += 1
            retcode = d.exec_inspect(inst)['ExitCode']
            time.sleep(1)
            if count > 15:
                raise ExecException("Command %s timed out, output: %s" % (cmd, output))

        if retcode != 0:
            raise ExecException("Command %s failed to execute, return code: %s" % (cmd, retcode), output)

        return output

    def inspect(self):
        if self.container:
            return d.inspect_container(container=self.container.get('Id'))

    def get_output(self, history=True):
        try:
            return d.logs(container=self.container)
        except:
            return d.attach(container=self.container, stream=False, logs=history)

    def remove_image(self, force=False):
        self.logger.info("Removing image %s" % self.image_id)
        d.remove_image(image=self.image_id, force=force)

    def copy_file_to_container(self, src_file, dest_folder):
        if not os.path.isabs(src_file):
            src_file = os.path.abspath(os.path.join(os.getcwd(), src_file))

        # The Docker library needs tar bytes to put_archive
        with tempfile.NamedTemporaryFile() as f:
            with tarfile.open(fileobj=f, mode='w') as t:
                t.add(src_file, arcname=os.path.basename(src_file), recursive=False)

            f.seek(0)

            d.put_archive(
                container=self.container['Id'],
                path=dest_folder,
                data=f.read())

    def _create_container(self, **kwargs):
        """ Creates a detached container for selected image """
        if self.running:
            self.logger.debug("Container is running")
            return

        volume_mount_points = None
        host_args = {}

        if self.volumes:
            volume_mount_points = []
            for volume in self.volumes:
                volume_mount_points.append(volume.split(":")[0])
            host_args['binds'] = self.volumes

        # update kwargs with env override
        kwargs_env = kwargs["environment"] if "environment" in kwargs else {}
        kwargs_env.update(self.environ)
        kwargs.update(dict(environment=kwargs_env))

        # 'env_json' is an environment dict packed into JSON, possibly supplied by
        # steps like 'container is started with args'
        if "env_json" in kwargs:
            env = json.loads(kwargs["env_json"])
            kwargs_env = kwargs["environment"] if "environment" in kwargs else {}
            kwargs_env.update(env)
            kwargs.update(dict(environment=kwargs_env))
            del kwargs["env_json"]

        self.logger.debug("Creating container from image '%s'..." % self.image_id)

        # we need to split kwargs to the args with belongs to create_host_config and
        # create_container - be aware - this moved to different place for new docker
        # python API
        host_c_args_names = docker.utils.utils.create_host_config.__code__.co_varnames
        host_c_args_names = list(host_c_args_names) + ['cpu_quota', 'cpu_period', 'mem_limit']
        for arg in host_c_args_names:
            if arg in kwargs:
                host_args[arg] = kwargs.pop(arg)
                try:
                    host_args[arg] = int(host_args[arg])
                except:
                    pass

        debug = " ".join(f"-e {k}={v}" for k,v in kwargs.get("environment").items())
        self.logger.debug(f"Creating docker container with arguments and image: {debug} {self.image_id}")
        self.container = d.create_container(image=self.image_id,
                                            detach=True,
                                            entrypoint=self.entrypoint,
                                            volumes=volume_mount_points,
                                            host_config=d.create_host_config(**host_args),
                                            **kwargs)

