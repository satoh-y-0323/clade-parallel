# Coding Conventions

## Target Language
- Python (3.10+)

## Base Standards
| Standard | Summary |
|---|---|
| PEP 8 | Official Python style guide. Covers indentation, line length, whitespace, naming, and import ordering. |
| PEP 257 | Docstring conventions. Defines how to write module-, class-, and function-level documentation. Implemented using Google Style docstrings. |
| PEP 484 / 604 | Type hint conventions. Requires type annotations on public APIs; uses the `X | None` union syntax introduced in Python 3.10 and built-in generic forms from Python 3.9+. |

## Naming Conventions
| Target | Rule | Example |
|---|---|---|
| Variable | `lower_snake_case` | `user_count`, `is_active` |
| Function / Method | `lower_snake_case` | `get_user()`, `calculate_total()` |
| Class | `PascalCase` | `UserAccount`, `HttpClient` |
| Constant | `UPPER_SNAKE_CASE` | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| Module / Package | `lower_snake_case` | `auth_utils.py`, `data_parser/` |
| Private member | Leading underscore `_name` | `_internal_cache`, `_validate()` |
| File | `lower_snake_case` with `.py` extension | `user_service.py`, `test_auth.py` |

## Formatting
- Indentation: 4 spaces (no tabs)
- Maximum line length: 88 characters (Black / Ruff default)
- Blank lines between top-level functions and classes: 2
- Blank lines between methods inside a class: 1
- Trailing newline at end of file: required
- String quotes: double quotes `"..."` (Black standard; single quotes only inside double-quoted strings)
- String formatting: f-strings preferred over `.format()` or `%`-style

## Comments
- Language: English
- Module / class / public function: Google Style docstring required
  ```python
  def fetch_user(user_id: int) -> User | None:
      """Fetch a user record by ID.

      Args:
          user_id: The unique identifier of the user.

      Returns:
          A User instance if found, or None if no record exists.

      Raises:
          DatabaseError: If the database connection fails.
      """
  ```
- Complex logic: inline comments on the same line or the line above; explain *why*, not *what*
- TODO / FIXME: `# TODO(author): description` format

## Imports and Dependencies
- Order (enforced by isort / Ruff):
  1. Standard library
  2. Third-party packages
  3. Local / project modules
- One import per line for `import` statements
- `from ... import` grouping is allowed within the same section
- No wildcard imports (`from module import *`)
- Unused imports must be removed

## Type Hints
- All public functions and methods must have full type annotations (parameters and return type)
- Use `X | None` instead of `Optional[X]` (Python 3.10+ syntax)
- Use built-in generic types: `list[str]`, `dict[str, int]`, `tuple[int, ...]` (Python 3.9+)
- `TypeAlias` for complex repeated types
- `typing.Protocol` preferred over abstract base classes where duck typing is sufficient

## Error Handling
- Catch specific exception types; avoid bare `except:` and broad `except Exception:` unless re-raised
- Always re-raise or log with context when catching unexpected exceptions
- Use custom exception classes inheriting from a project-level base exception for domain errors
- Do not swallow exceptions silently

## Mutable Default Arguments
- Mutable default arguments (list, dict, set) are prohibited
  ```python
  # Bad
  def add_item(items: list = []) -> list: ...

  # Good
  def add_item(items: list | None = None) -> list:
      if items is None:
          items = []
  ```

## Project File Structure
```
project-root/
├── src/
│   └── <package_name>/   # e.g., clade_parallel
│       ├── __init__.py
│       └── ...
├── tests/
│   ├── __init__.py
│   └── test_*.py
├── pyproject.toml
└── ...
```
- Use `src/` layout to prevent accidental imports of local source before installation
- Every package directory must contain `__init__.py`

## Test Conventions
- Test framework: pytest
- Test file naming: `test_<module_name>.py` (e.g., `test_user_service.py`)
- Test function naming: `test_<scenario_description>` in `lower_snake_case` (e.g., `test_returns_none_when_user_not_found`)
- Test structure: Arrange - Act - Assert (AAA pattern)
- One logical assertion per test where practical; use `pytest.raises` for exception tests
- Fixtures defined in `conftest.py` at the appropriate scope level
- Parametrized tests use `@pytest.mark.parametrize`

## Toolchain
| Tool | Role | Configuration |
|---|---|---|
| Black or Ruff format | Auto-formatter | Line length 88, double quotes |
| Ruff (linter) | Linting (flake8 + pylint + isort + pyupgrade equivalent) | Configured in `pyproject.toml` |
| mypy or Pyright | Static type checking | Strict mode recommended |
| pytest | Test runner | `tests/` directory |

All tools should be configured in `pyproject.toml` under their respective `[tool.*]` sections.

## Custom Rules
None.

## Exclusions / Overrides
None. All rules from PEP 8, PEP 257, and PEP 484 / 604 apply as-is.
