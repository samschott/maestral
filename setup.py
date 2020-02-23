import sys
import os
import os.path as osp
import platform
import tempfile
from setuptools import setup, find_packages
from maestral import __version__, __author__, __url__


# check for running daemons before updating to prevent
# incompatible versions of CLI / GUI and daemon

def get_home_dir():
    try:
        path = osp.expanduser('~')
    except Exception:
        path = ''

    if osp.isdir(path):
        return path
    else:
        for env_var in ('HOME', 'USERPROFILE', 'TMP'):
            path = os.environ.get(env_var, '')
            if osp.isdir(path):
                return path
            else:
                path = ''

        if not path:
            raise RuntimeError('Please set the environment variable HOME to '
                               'your user/home directory.')


_home_dir = get_home_dir()


def _to_full_path(path, subfolder, filename, create):

    if subfolder:
        path = osp.join(path, subfolder)

    if create:
        os.makedirs(path, exist_ok=True)

    if filename:
        path = osp.join(path, filename)

    return path


def get_conf_path(subfolder=None, filename=None, create=True):
    if platform.system() == 'Darwin':
        conf_path = osp.join(get_home_dir(), 'Library', 'Application Support')
    else:
        fallback = osp.join(get_home_dir(), '.config')
        conf_path = os.environ.get('XDG_CONFIG_HOME', fallback)

    return _to_full_path(conf_path, subfolder, filename, create)


def get_runtime_path(subfolder=None, filename=None, create=True):
    if platform.system() == 'Darwin':
        runtime_path = get_conf_path(create=False)
    else:
        fallback = os.environ.get('XDG_CACHE_HOME', osp.join(_home_dir, '.cache'))
        runtime_path = os.environ.get('XDG_RUNTIME_DIR', fallback)

    return _to_full_path(runtime_path, subfolder, filename, create)


def get_old_runtime_path(subfolder=None, filename=None, create=True):
    if platform.system() == 'Darwin':
        runtime_path = tempfile.gettempdir()
    else:
        fallback = os.environ.get('XDG_CACHE_HOME', osp.join(_home_dir, '.cache'))
        runtime_path = os.environ.get('XDG_RUNTIME_DIR', fallback)

    return _to_full_path(runtime_path, subfolder, filename, create)


def list_configs():
    configs = []
    for file in os.listdir(get_conf_path('maestral')):
        if file.endswith('.ini'):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs


running_daemons = []

for config in list_configs():
    pid_file = get_runtime_path('maestral', config + '.pid')
    old_pid_file = get_old_runtime_path('maestral', config + '.pid')
    if osp.exists(pid_file) or osp.exists(old_pid_file):
        running_daemons.append(config)

if running_daemons:
    sys.stderr.write(f"""
Maestral daemons with the following configs are running:

{', '.join(running_daemons)}

Please stop the daemons before updating to ensure a clean upgrade
of config files and compatibility been the CLI and daemon.
    """)
    sys.exit(1)


# proceed with actual install

setup(
    name='maestral',
    version=__version__,
    description='Open-source Dropbox client for macOS and Linux.',
    url=__url__,
    author=__author__,
    author_email='ss2151@cam.ac.uk',
    license='MIT',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    packages=find_packages(),
    package_data={
        'maestral': [
            'resources/*',
        ],
    },
    setup_requires=['wheel'],
    install_requires=[
        'atomicwrites',
        'bugsnag',
        'click>=7.0',
        'dropbox>=9.4.0',
        'importlib_metadata;python_version<"3.8"',
        'keyring>=19.0.0',
        'keyrings.alt>=3.0.0',
        'lockfile',
        'packaging',
        'Pyro5>=5.7',
        'requests',
        'rubicon-objc>=0.3.1;sys_platform=="darwin"',
        'sdnotify',
        'u-msgpack-python',
        'watchdog>=0.9.0',
    ],
    extras_require={
        'syslog': [
            'systemd-python',
        ],
        'gui': [
            'maestral-qt>=0.6.1-dev1'
        ],
    },
    zip_safe=False,
    entry_points={
        'console_scripts': ['maestral=maestral.cli:main'],
    },
    python_requires='>=3.6',
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Operating System :: Unix',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3 :: Only',
    ],
    data_files=[
        ('share/icons/hicolor/512x512/apps', ['maestral/resources/maestral.png'])
    ],
)
