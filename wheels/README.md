# Bundled wheels

## `midas_index-0.11.0-py3-none-any.whl`

`midas-index` (a transitive dependency declared by `midas-defect`; midas-gui
never imports it) publishes **only an sdist** to PyPI from 0.5.x on. pip has to
compile it with CMake, which fails on Windows without Visual Studio Build Tools
(`'nmake' '-?' failed`) and needs cmake + a compiler on macOS/Linux too.

This wheel is the unmodified upstream 0.11.0 sources built **without the
optional C indexer**, so it is pure Python (`py3-none-any`) and installs on any
OS. `midas_index.backend_c.available()` is then `False` and the Python/numba
backend is used (the default).

Rebuild (BSD-3-Clause, upstream: https://github.com/marinerhemant/MIDAS):

```
pip download --no-deps --no-binary=:all: -d dl midas-index==0.11.0
pip wheel --no-deps -C wheel.cmake=false -w wheels dl/midas_index-0.11.0.tar.gz
```

| file | sha256 |
|---|---|
| sdist `midas_index-0.11.0.tar.gz` (PyPI) | `f361281676997d1c6dff75d47ab1beaebba80e681a3e135cfd934403cf8014f9` |
| `midas_index-0.11.0-py3-none-any.whl` (this) | `1fb64a599747e80c0b75652670cd095e6fe998609e399e390d9cc0bf9ea4fe2b` |

`environment.yml` / `requirements.txt` reference it by relative path, so run
`conda env create -f environment.yml` / `pip install -r requirements.txt` **from
the repo root**. Drop this directory once PyPI ships wheels for every OS.
