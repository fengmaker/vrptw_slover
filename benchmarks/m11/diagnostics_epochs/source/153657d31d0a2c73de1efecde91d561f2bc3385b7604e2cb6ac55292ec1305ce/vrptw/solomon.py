"""Parser for the original seven-column Solomon TXT format only."""

from pathlib import Path

from .problem import SCALE, Customer, Instance


def read_solomon(path: str | Path) -> Instance:
    """Parse an original Solomon instance and normalize all times to ticks."""
    path = Path(path)
    if path.suffix.lower() != ".txt":
        raise ValueError("read_solomon requires an original Solomon .txt file")
    lines = [(number, line.strip()) for number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ) if line.strip()]
    if not lines:
        raise ValueError(f"{path}: empty Solomon file")
    name = lines[0][1]
    try:
        vehicle_at = next(i for i, (_, line) in enumerate(lines) if line == "VEHICLE")
        customer_at = next(i for i, (_, line) in enumerate(lines) if line == "CUSTOMER")
    except StopIteration as exc:
        raise ValueError(f"{path}: missing VEHICLE or CUSTOMER section") from exc
    if customer_at <= vehicle_at:
        raise ValueError(f"{path}: CUSTOMER must follow VEHICLE")

    fleet_lines = [entry for entry in lines[vehicle_at + 1:customer_at]
                   if not entry[1].upper().startswith("NUMBER")]
    if len(fleet_lines) != 1:
        raise ValueError(f"{path}: expected one NUMBER CAPACITY row")
    fleet_line, fleet_text = fleet_lines[0]
    try:
        vehicle_count, capacity = (int(part) for part in fleet_text.split())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path}:{fleet_line}: expected vehicle count and capacity") from exc

    data_lines = [entry for entry in lines[customer_at + 1:]
                  if not entry[1].upper().startswith("CUST NO.")]
    if len(data_lines) < 2:
        raise ValueError(f"{path}: expected depot and at least one customer")
    customers = []
    for line_number, text in data_lines:
        fields = text.split()
        if len(fields) != 7:
            raise ValueError(f"{path}:{line_number}: customer row needs seven integer fields")
        try:
            node, x, y, demand, ready, due, service = map(int, fields)
            customers.append(Customer(node, x, y, demand, SCALE * ready, SCALE * due, SCALE * service))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return Instance(name, vehicle_count, capacity, tuple(customers))
