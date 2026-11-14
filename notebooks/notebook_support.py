"""Small presentation helpers for the cricket economy investigation."""

from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display

COLORS = {"charged": "#24745a", "naive": "#b06d45", "excluded": "#4a719c"}


def theme() -> None:
    """Apply consistent notebook typography and chart styling."""
    sns.set_theme(style="whitegrid", context="notebook", font="DejaVu Sans")
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "text.parse_math": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#e3e9e5",
        }
    )


def prose(text: str) -> None:
    """Display result-derived Markdown."""
    display(Markdown(text))


def finish(fig: plt.Figure, title: str, subtitle: str, filename: str, mode: str) -> None:
    """Add source context, save the rendered figure, and display it."""
    fig.suptitle(title, x=0.07, y=0.98, ha="left", fontsize=18, weight="bold", color="#253d31")
    fig.text(0.07, 0.92, subtitle, fontsize=10.5, color="#52675a")
    fig.text(
        0.07,
        0.025,
        f"Source: Cricsheet · match 1535465 · {mode} · ODC-By 1.0\n"
        "Real vgi-cricket through Haybarn / VGI; SQL calculations",
        fontsize=9,
        color="#52675a",
    )
    directory = Path("validation/charts")
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / filename, dpi=150)
    plt.show()
