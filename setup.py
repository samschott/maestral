from setuptools import setup, find_packages

setup(name="sisyphosdbx",
      version="v0.1.0",
      description="Open-source Dropbox client for macOS and Linux.",
      url="https://github.com/SamSchott/sisyphosdbx",
      author="Sam Schott",
      author_email="ss2151@cam.ac.uk",
      licence="MIT",
      long_description=open("README.md").read(),
      packages=find_packages(),
      install_requires=[
          "dropbox",
          "watchdog",
          ],
      zip_safe=False,
      scripts=['bin/sisyphosdbx'],
      )
