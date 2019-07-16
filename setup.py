from setuptools import setup, find_packages


def get_version(relpath):
    """Read version info from a file without importing it"""
    from os.path import dirname, join

    if "__file__" not in globals():
        # Allow to use function interactively
        root = "."
    else:
        root = dirname(__file__)

    # The code below reads text file with unknown encoding in
    # in Python2/3 compatible way. Reading this text file
    # without specifying encoding will fail in Python 3 on some
    # systems (see http://goo.gl/5XmOH). Specifying encoding as
    # open() parameter is incompatible with Python 2

    # cp437 is the encoding without missing points, safe against:
    #   UnicodeDecodeError: 'charmap' codec can't decode byte...

    for line in open(join(root, relpath), "rb"):
        line = line.decode("cp437")
        if "__version__" in line:
            if '"' in line:
                return line.split('"')[1]
            elif "'" in line:
                return line.split("'")[1]


setup(
    name="maestral",
    version=get_version("maestral/main.py"),
    description="Open-source Dropbox client for macOS and Linux.",
    url="https://github.com/SamSchott/maestral",
    author="Sam Schott",
    author_email="ss2151@cam.ac.uk",
    license="MIT",
    long_description=open("README.md").read(),
    long_description_content_type='text/markdown',
    packages=find_packages(),
    package_data={
            "maestral": [
                    "gui/resources/*.ui",
                    "gui/resources/*.icns",
                    "gui/resources/*.png",
                    "gui/resources/*.svg",
                    "utils/maestral.desktop",
                    "utils/com.maestral.loginscript.plist",
                    "bin/*.sh",
                    ],
            },
    install_requires=[
        "click",
        "dropbox",
        "watchdog",
        "blinker",
        "requests",
        "u-msgpack-python",
        "keyring",
        "keyrings.alt"
        ],
    zip_safe=False,
    entry_points={
      "console_scripts": ["maestral=maestral.console_script:main"],
      },
    scripts=['bin/maestral-gui'],
    python_requires='>=3.6',
    )
