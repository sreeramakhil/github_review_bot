# calculator.py

# BAD CODE (The AI Review Bot should flag these issues):
def calculate_string(expression, history=[]):
    try:
        result = eval(expression)
        history.append(result)
        return result
    except Exception:
        pass


# GOOD CODE (The AI Review Bot should praise or ignore this):
def safe_divide(a: float, b: float) -> float | None:
    """
    Safely divides two numbers and returns None if dividing by zero.
    """
    try:
        return a / b
    except ZeroDivisionError:
        return None
