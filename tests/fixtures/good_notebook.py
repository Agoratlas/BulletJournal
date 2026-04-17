import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _(mo):
    mo.md('# Notebook docs')
    return


@app.cell
def _():
    limit = artifacts.pull(name='limit', data_type=int, default=3)
    return limit


@app.cell
def _(limit, pd):
    frame = pd.DataFrame({'value': list(range(limit))})
    artifacts.push(frame, name='frame', data_type=pd.DataFrame)
    artifacts.push('ok', name='summary', data_type=str)
    return


if __name__ == '__main__':
    app.run()
