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


@app.cell
def _():
    sample_count = artifacts.pull(
        name='sample_count',
        data_type=int,
        default=10,
        description='How many sample rows to generate',
    )
    return sample_count


@app.cell
def _(pd, sample_count):
    frame = pd.DataFrame({'value': list(range(sample_count))})
    artifacts.push(
        frame,
        name='sample_df',
        data_type=pd.DataFrame,
        description='Sample output frame',
    )
    return frame


@app.cell
def _(assets, frame):
    assets.push(
        assets.DataFrame(frame),
        name='sample_table',
        title='Sample table',
        description='Interactive preview of the sample output frame',
        asset_type=assets.DataFrame,
    )
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
