"""Build a four-viewport VTK.js comparison with one camera and common controls."""

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(experiment, variants):
    experiment = Path(experiment)
    payload = []
    for name, gap, flow in variants:
        folder = experiment / name / "visualization/charcoal"
        page = folder / "charcoal-flow.html"
        item = {"name": name, "label": f"{gap * 100:g} см / {flow:g} м³/ч", "data": None}
        if page.exists() and (experiment / name / "COMPLETED.json").exists():
            metadata = json.loads((folder / "metadata.json").read_text())
            match = re.search(r"<script>const datasets=(.*?);</script>", page.read_text(), re.S)
            if not match:
                raise ValueError(f"Missing embedded VTK datasets: {page}")
            if metadata["iteration"] != metadata["summary"]["iteration"]:
                raise ValueError(f"Iteration mismatch: {folder}")
            item.update(
                data=json.loads(match[1]),
                iteration=metadata["iteration"],
                temperature_C=metadata["summary"]["outlet_mean_T_C"],
            )
        payload.append(item)
    template = (ROOT / "scripts/comparison_viewer_template.html").read_text()
    titles = []
    for i, item in enumerate(payload):
        details = (
            f"Итерация {item['iteration']} · выход {item['temperature_C']:.2f} °C"
            if item["data"]
            else "Расчёт / экспорт ещё не завершён"
        )
        titles.append(
            f'<header class="title title-{i}"><a href="{item["name"]}/visualization/charcoal/charcoal-flow.html">{html.escape(item["label"])}</a><small>{details}</small></header>'
        )
    text = template.replace("__TITLES__", "".join(titles)).replace(
        "__VARIANTS_JSON__",
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).replace(
            "</", "<\\/"
        ),
    )
    destination = experiment / "comparison.html"
    destination.write_text(text)
    return destination
