# Optional native move evaluator

The `vrptw._native.MoveEvaluator` extension implements the exact feasible
single-route and two-route move neighborhoods used by `vrptw.local_search`.
It uses the same integer distance and time data already prepared by Python;
it does not generate distances or depend on an external routing solver.

The default package build remains pure Python. To build the extension on
Windows with a configured MSVC C++ toolchain and pybind11 installed, run:

```powershell
$env:VRPTW_BUILD_NATIVE = "1"
python -m pip install "pybind11==3.0.1" "setuptools==75.6.0"
python setup.py build_ext --inplace
```

Unset `VRPTW_BUILD_NATIVE` to leave the extension out of subsequent builds.

The constructor takes `(distance, demand, ready, due, service, capacity,
routes)`, with depot at index 0 and routes containing customer indices. `delta`
returns an exact integer cost change or `None` for an infeasible/empty-route
candidate. `apply` accepts only a feasible move and updates the evaluator.
`scan(operators, strategy="first", remaining_seconds=None, neighbours=None)`
returns the best strictly improving move according to the deterministic
Python enumeration order, together with counters and a timeout flag. A timed
out scan returns no move so a partial best-so-far is never accepted.

Input values are checked against conservative signed 64-bit arithmetic bounds.
Routes may include empty slots, but all customers must occur exactly once and
every nonempty source route must be feasible.

The conservative bounds are 100,000 customers and scalar values at most
`10^12`. Python's `auto` mode falls back to its arbitrary-precision evaluator
outside those bounds. Source and destination route slots must both be nonempty
for a fixed-fleet move. The binding is module-local so independently frozen
experimental packages can coexist with the public validator in one process.

The build uses the documented [pybind11 setuptools helpers](https://pybind11.readthedocs.io/en/stable/compiling.html),
C++17 and `/O2` on MSVC (`-O3` elsewhere). Windows builds require the Visual
Studio C++ Build Tools and Windows SDK. Use the same CPython version and
architecture for the build and solver interpreter. The binary is generated
locally and ignored by Git; rebuilding is supported, byte-identical output is
not promised. No substantive PyVRP source was copied into this implementation.
