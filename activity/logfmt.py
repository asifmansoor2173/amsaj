import json
import logging


class JsonLinesFormatter(logging.Formatter):
    """Writes each activity as one JSON object per line (easy to grep, import or ship to a log tool)."""

    def format(self, record):
        data = getattr(record, "record_data", None) or {"message": record.getMessage()}
        return json.dumps(data, ensure_ascii=False, default=str)
