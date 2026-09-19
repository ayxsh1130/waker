def calculate(values: list[int]) -> dict:
    return {"count": len(values), "sum": sum(values), "mean": sum(values) / max(1, len(values))}
