"""Russian number agreement and short phrases built by code (ages, relative dates)."""

from __future__ import annotations


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(1, «день», «дня», «дней») → «день»; 3 → «дня»; 11 → «дней»."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def years(n: int) -> str:
    return f"{n} {plural(n, 'год', 'года', 'лет')}"


def months(n: int) -> str:
    return f"{n} {plural(n, 'месяц', 'месяца', 'месяцев')}"


def days(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def format_age(age_months: int) -> str:
    """96 → «8 лет», 18 → «1 год 6 месяцев», 4 → «4 месяца», 0 → «до 1 месяца»."""
    if age_months <= 0:
        return "до 1 месяца"
    y, m = divmod(age_months, 12)
    if not y:
        return months(m)
    return years(y) if not m else f"{years(y)} {months(m)}"


def relative_day(delta_days: int) -> str:
    """Days from the event to today: 0 → «сегодня», 2 → «2 дня назад», −3 → «через 3 дня»."""
    if delta_days == 0:
        return "сегодня"
    if delta_days == 1:
        return "вчера"
    if delta_days == -1:
        return "завтра"
    n = abs(delta_days)
    if n <= 60:
        span = days(n)
    elif n < 730:
        span = months(n // 30)
    else:
        span = years(n // 365)
    return f"{span} назад" if delta_days > 0 else f"через {span}"
