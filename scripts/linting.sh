#!/bin/bash
# As described in `CONTRIBUTING.md`, this script checks the source codes for formatting,
# coding style and type hints. The `-u` option enables automatic formatting by `black`.
# This script is taken from the Optuna project.

set -e

res_pip_list=$(pip3 freeze)
missing_dependencies=()
if [ ! "$(echo $res_pip_list | grep black)" ] ; then
  missing_dependencies+=(black)
fi
if [ ! "$(echo $res_pip_list | grep flake8)" ] ; then
  missing_dependencies+=(flake8)
fi
if [ ! "$(echo $res_pip_list | grep mypy)" ] ; then
  missing_dependencies+=(mypy)
fi
if [ ! ${#missing_dependencies[@]} -eq 0 ]; then
  echo "The following dependencies are missing:" "${missing_dependencies[@]}"
  read -p "Would you like to install the missing dependencies? (y/N): " yn
  case "$yn" in [yY]*) ;; *) echo "abort." ; exit ;; esac
  pip3 install "${missing_dependencies[@]}"
fi

update=0
while getopts "u" OPT
do
  case $OPT in
    u) update=1
       ;;
    *) ;;
  esac
done

set +e

target="src tests"
mypy_target="src"
res_all=0

res_black=$(black $target --check --diff 2>&1)
if [ $? -eq 1 ] ; then
  if [ $update -eq 1 ] ; then
    echo "black failed. The code will be formatted by black."
    black .
  else
    echo "$res_black"
    echo "black failed."
    res_all=1
  fi
else
  echo "black succeeded."
fi

res_flake8=$(flake8 $target)
if [ $? -eq 1 ] ; then
  echo "$res_flake8"
  echo "flake8 failed."
  res_all=1
else
  echo "flake8 succeeded."
fi

res_mypy=$(mypy $mypy_target)
if [ $? -eq 1 ] ; then
  echo "$res_mypy"
  echo "mypy failed."
  res_all=1
else
  echo "mypy succeeded."
fi

if [ $res_all -eq 1 ] ; then
  exit 1
fi
