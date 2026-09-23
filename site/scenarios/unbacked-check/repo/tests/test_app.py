from app import greet


def test_greet() -> None:
    assert greet().startswith("hello")
