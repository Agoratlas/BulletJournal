export type HistogramGroupValue = string | number | boolean

export function nextHistogramGroupSelection(
  selectedGroups: HistogramGroupValue[],
  clickedGroup: HistogramGroupValue,
  additive: boolean,
): HistogramGroupValue[] {
  const alreadySelected = selectedGroups.some((group) => group === clickedGroup)
  if (!additive) {
    return alreadySelected && selectedGroups.length === 1 ? [] : [clickedGroup]
  }
  return alreadySelected
    ? selectedGroups.filter((group) => group !== clickedGroup)
    : [...selectedGroups, clickedGroup]
}

export function histogramSelectionFromGroupClick(
  selectedGroups: HistogramGroupValue[],
  clickedGroup: HistogramGroupValue,
  additive: boolean,
) {
  return {
    selectedBarIndexes: [] as number[],
    selectedGroups: nextHistogramGroupSelection(selectedGroups, clickedGroup, additive),
  }
}

export function histogramSelectionFromRange(selectedBarIndexes: number[]) {
  return { selectedBarIndexes, selectedGroups: [] as HistogramGroupValue[] }
}

export function histogramSegmentIsSelected(
  group: HistogramGroupValue | null,
  binIndex: number,
  selectedGroups: HistogramGroupValue[],
  selectedBarIndexes: number[],
): boolean {
  return (!selectedGroups.length || (group !== null && selectedGroups.includes(group)))
    && (!selectedBarIndexes.length || selectedBarIndexes.includes(binIndex))
}

export function histogramGroupFromLegendLabel(
  bins: Array<{ group?: HistogramGroupValue | null; group_label?: string | null }>,
  label: unknown,
): HistogramGroupValue | null {
  if (typeof label !== 'string' && typeof label !== 'number' && typeof label !== 'boolean') {
    return null
  }
  const bin = bins.find((entry) => (entry.group_label ?? String(entry.group)) === String(label))
  return bin?.group ?? null
}
