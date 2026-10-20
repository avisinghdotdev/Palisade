"""Apply every pending migration: `python -m palisade_api.migrate`."""

import logging

from alembic import command
from alembic.config import Config


def upgrade_to_head() -> None:
    config = Config()
    config.set_main_option("script_location", "palisade_api:migrations")
    command.upgrade(config, "head")


if __name__ == "__main__":
    # Alembic reports each migration it applies through logging.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    upgrade_to_head()
