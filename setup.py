import sys
import os.path as osp
from setuptools import setup, find_packages
from maestral import __version__, __author__, __url__
from maestral.utils.appdirs import get_runtime_path, get_old_runtime_path
from maestral.config.base import list_configs

# abort install if there are running daemons
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
install_requires = [
    'atomicwrites',
    'bugsnag',
    'click>=7.1.1',
    'dropbox>=9.4.0, <=9.5.0',
    'importlib_metadata;python_version<"3.8"',
    'keyring>=19.0.0',
    'keyrings.alt>=3.0.0',
    'lockfile',
    'packaging',
    'pathspec',
    'Pyro5>=5.7',
    'requests',
    'rubicon-objc>=0.3.1;sys_platform=="darwin"',
    'sdnotify',
    'setuptools',
    'u-msgpack-python',
    'watchdog>=0.9.0',
]

gui_requires = ['maestral-qt==0.6.3']
syslog_requires = ['systemd-python']

# if GUI is installed, always update it as well
try:
    import maestral_qt  # noqa: F401
except ImportError:
    pass
else:
    install_requires += gui_requires


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
    install_requires=install_requires,
    extras_require={
        'gui': gui_requires,
        'syslog': syslog_requires,
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
