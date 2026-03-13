/** Nodeglow ECharts theme */
export const nodeglowTheme = {
  backgroundColor: 'transparent',
  textStyle: {
    color: '#94A3B8',
    fontFamily: 'Inter, sans-serif',
    fontSize: 12,
  },
  title: {
    textStyle: { color: '#F1F5F9', fontSize: 14, fontWeight: 600 },
  },
  legend: {
    textStyle: { color: '#94A3B8' },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: '#1E2433' } },
    axisTick: { show: false },
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E2433', type: 'dashed' as const } },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E2433', type: 'dashed' as const } },
  },
  tooltip: {
    backgroundColor: '#1A1F2E',
    borderColor: '#2A3144',
    textStyle: { color: '#F1F5F9', fontSize: 12 },
    extraCssText: 'backdrop-filter: blur(12px); border-radius: 8px;',
  },
  color: [
    '#38BDF8', '#A78BFA', '#34D399', '#FBBF24', '#F87171',
    '#60A5FA', '#FB923C', '#E879F9', '#2DD4BF', '#818CF8',
  ],
  grid: {
    left: 8, right: 8, top: 32, bottom: 8,
    containLabel: true,
  },
  line: {
    smooth: true,
    symbolSize: 4,
    lineStyle: { width: 2 },
    areaStyle: { opacity: 0.08 },
  },
  bar: {
    barMaxWidth: 24,
    itemStyle: { borderRadius: [4, 4, 0, 0] },
  },
};
