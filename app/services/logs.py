"""Запись в журнал так, чтобы сбой самого журнала не ломал приложение.

Стандартные обработчики (StreamHandler, FileHandler) глушат свои ошибки внутри
`emit`, и с ними пайплайн прерваться не может. Но обработчик, установленный
посторонним кодом, бросает исключение наружу — и тогда запись обрывает
сравнение: сбой журнала превращается в ложную ошибку сравнения
(spec: application-logging, «Журнал не влияет на результат сравнения»).

Поэтому все записи приложения проходят через эти функции. Причина сбоя попадает
в журнал, недоступность журнала — нет.
"""

from __future__ import annotations

import contextlib
import logging


def failure_reason(exc: BaseException) -> str:
    """Причина сбоя для журнала: тип исключения без его текста.

    Текст исключения сторонней библиотеки может содержать адрес сервиса или ключ
    API, а журнал уходит в вывод процесса и в журналы CI. Тип даёт нужное
    различение причин (сетевая ошибка, таймаут, ошибка разбора) и не может
    утечь (spec: application-logging).
    """
    return type(exc).__name__


def log_warning(logger: logging.Logger, message: str, *args) -> None:
    """Предупреждение о проглоченном сбое; ошибка журнала не пробрасывается.

    `stacklevel=2` возвращает в запись вызывающую функцию: без него обёртка
    подменила бы её собой и требование «место (модуль и функция)» не
    выполнялось бы.
    """
    with contextlib.suppress(Exception):
        logger.warning(message, *args, stacklevel=2)


def log_error(logger: logging.Logger, message: str, *args) -> None:
    """Ошибка с трассировкой; ошибка журнала не пробрасывается."""
    with contextlib.suppress(Exception):
        logger.error(message, *args, exc_info=True, stacklevel=2)
