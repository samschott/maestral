import sys
import os
from setuptools import setup, find_packages
from maestral import __version__, __author__, __url__
from maestral.config.base import list_configs
from maestral.utils.appdirs import get_runtime_path, get_old_runtime_path

CURRENT_PYTHON = sys.version_info[:2]
REQUIRED_PYTHON = (3, 6)

# check for running daemons before updating to prevent
# incompatible versions of CLI / GUI and daemon

running_daemons = []

for config in list_configs():
    pid_file = get_runtime_path("maestral", config + ".pid")
    old_pid_file = get_old_runtime_path("maestral", config + ".pid")
    if os.path.exists(pid_file) or os.path.exists(old_pid_file):
        running_daemons.append(config)

if running_daemons:
    sys.stderr.write(f"""
Maestral daemons with the following configs are running:

{', '.join(running_daemons)}

Please stop the daemons before updating, you may otherwise not be able
to use the new command line interface with the old daemon.
    """)
    sys.exit(1)


setup(
    name='maestral',
    version=__version__,
    description='Open-source Dropbox client for macOS and Linux.',
    url=__url__,
    author='Sam Schott',
    author_email=__author__,
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
            'maestral-cocoa==0.1.1-dev1;sys_platform=="darwin"',
            'maestral-qt==0.6.1-dev1;sys_platform=="linux"'
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
