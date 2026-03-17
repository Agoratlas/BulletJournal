import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    value = artifacts.pull(name='value', data_type=int, default=1)
    return value


@app.cell
def _(
    broken =


if __name__ == '__main__':
    app.run()
