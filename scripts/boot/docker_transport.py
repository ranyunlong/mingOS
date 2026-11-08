"""Docker CLI transport pinned to an explicit executable, endpoint and config."""
import io
import ntpath
import os
import posixpath
import subprocess

from qualification_image_archive import _docker_run


def _text(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('nonempty text without control characters required')
    return value


def _path(value):
    _text(value)
    drive, tail = ntpath.splitdrive(value)
    windows_absolute = bool(drive) and tail.startswith(('\\', '/'))
    if not (posixpath.isabs(value) or windows_absolute):
        raise ValueError('absolute path required')
    if '..' in value.replace('\\', '/').split('/'):
        raise ValueError('path traversal is forbidden')
    return value


def _stdin(source):
    if source is None:
        return
    if (isinstance(source, io.TextIOBase) or 'b' not in getattr(source, 'mode', '')
            or getattr(source, 'closed', True) or not source.readable()):
        raise ValueError('stdin must be an open binary file')
    try:
        source.fileno()
    except (OSError, ValueError) as error:
        raise ValueError('stdin must be an open binary file with a descriptor') from error


def _environment():
    return {key: value for key, value in os.environ.items()
            if not (key.upper().startswith(('DOCKER_', 'TLS_'))
                    or key.upper() in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                                       'FTP_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR'})}


class DockerTransport:
    """Run Docker without ambient context or connection overrides."""

    def __init__(self, executable: str, endpoint: str, config_directory: str):
        self.executable = _path(executable)
        self.config_directory = _path(config_directory)
        _text(endpoint)
        if endpoint.startswith('unix:///'):
            socket = endpoint[len('unix://'):]
            _path(socket)
            if socket == '/' or socket.startswith('//') or '\\' in socket:
                raise ValueError('explicit Unix socket path required')
        elif endpoint.startswith('npipe:////./pipe/'):
            pipe = endpoint[len('npipe:////./pipe/'):]
            if not pipe or '..' in pipe.replace('\\', '/').split('/') or '\\' in pipe or pipe.startswith('/'):
                raise ValueError('explicit named pipe required')
        else:
            raise ValueError('explicit unix or npipe endpoint required')
        self.endpoint = endpoint

    def argv(self, args: list[str]) -> list[str]:
        if not isinstance(args, list):
            raise ValueError('Docker arguments must be a list')
        for arg in args:
            _text(arg)
            option = arg.split('=', 1)[0]
            if (option in {'--host', '--context', '--config', '--tls', '--tlsverify',
                           '--tlscacert', '--tlscert', '--tlskey'}
                    or arg.startswith(('-H', '-c'))):
                raise ValueError('Docker connection overrides are forbidden')
        return [self.executable, '--host', self.endpoint, '--config', self.config_directory, *args]

    def run(self, args: list[str], *, timeout: int, stdin=None) -> subprocess.CompletedProcess:
        command = self.argv(args)
        _stdin(stdin)
        return subprocess.run(command, timeout=timeout, stdin=stdin, env=_environment(),
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def migration_runner(self, receipt, key, command, timeout, **kwargs):
        if set(kwargs) - {'stdin'}:
            raise ValueError('only stdin is permitted for the migration runner')
        if not isinstance(command, list) or not command or command[0] != self.executable:
            raise ValueError('migration executable must match transport executable')
        explicit_command = self.argv(command[1:])
        source = kwargs.get('stdin')
        _stdin(source)
        return _docker_run(receipt, key, explicit_command, timeout, stdin=source,
                           env=_environment(), shell=False)

