"""Optional build entry point for the pybind11 native move evaluator.

Pure-Python installs need no compiler or pybind11. Set VRPTW_BUILD_NATIVE=1
when explicitly building the acceleration module.
"""

import os
import sys

from setuptools import setup

extensions = []
commands = {}
if os.environ.get("VRPTW_BUILD_NATIVE") == "1":
    from pybind11.setup_helpers import Pybind11Extension, build_ext

    compile_args = ["/O2"] if sys.platform == "win32" else ["-O3"]
    extensions = [
        Pybind11Extension(
            "vrptw._native",
            ["native/moves.cpp"],
            cxx_std=17,
            extra_compile_args=compile_args,
        )
    ]
    commands = {"build_ext": build_ext}

setup(ext_modules=extensions, cmdclass=commands, include_package_data=True)
