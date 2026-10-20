import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Custom BulletJournal notebook')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md("""
    # Custom BulletJournal notebook

    Generic notebook to implement custom data processing.
    """)
    return


# Notebook inputs are inserted here.


@app.cell
def _():
    # Publish an output artifact
    # artifacts.push(
    #     result,
    #     name='result',
    #     data_type=pd.DataFrame,
    #     description='Describe the output artifact',
    # )
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
