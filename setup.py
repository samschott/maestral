import sys
from setuptools import setup, find_packages
from maestral import __version__, __author__, __url__

CURRENT_PYTHON = sys.version_info[:2]
REQUIRED_PYTHON = (3, 6)

# This check and everything above must remain compatible with Python 2.7.
if CURRENT_PYTHON < REQUIRED_PYTHON:
    # noinspection PyStringFormat
    sys.stderr.write("""
==========================
Unsupported Python version
==========================
Maestral requires Python {}.{} or higher, but you're trying to install
it on Python {}.{}. This may be because you are using a version of pip
that doesn't understand the python_requires classifier. Make sure you
have pip >= 9.0 and setuptools >= 24.2, then try again:
    $ python3 -m pip install --upgrade pip setuptools
    $ python3 -m pip install maestral

""".format(*(REQUIRED_PYTHON + CURRENT_PYTHON)))
    sys.exit(1)


# check for running daemons before updating to prevent
# incompatible versions of CLI / GUI and daemon
from maestral.config.base import list_configs
from maestral.daemon import get_maestral_pid


running_daemons = tuple(c for c in list_configs() if get_maestral_pid(c))

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
        ('share/systemd/user/', ['maestral/resources/maestral@.service']),
        ('share/icons/hicolor/512x512/apps', ['maestral/resources/maestral.png'])
    ],
)
