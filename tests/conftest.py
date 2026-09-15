import pytest

from sar_plimsoll.paths import project_root
from sar_plimsoll.scoring.rubric import Rubric, load_rubric


@pytest.fixture(scope="session")
def rubric() -> Rubric:
    return load_rubric(project_root() / "rubric" / "v1.yaml")
