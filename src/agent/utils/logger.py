import logging

_LOGGER_INITIALIZED = False


def setup_logging(level=logging.INFO) -> None:
    global _LOGGER_INITIALIZED

    if _LOGGER_INITIALIZED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    _LOGGER_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
