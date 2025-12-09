def check_documentation_exists(source_file):
    """Check if documentation file exists for Python source file."""
    expected_doc = (source_file.parent / f"{source_file.stem}.md"
                    if source_file.name == '__init__.py'
                    else source_file.with_suffix('.md'))
    if expected_doc.exists():
        return None
    return f"Missing documentation: {expected_doc.relative_to(source_file.parents[2])}"
