declare module "echarts-for-react" {
  import type { CSSProperties, ReactElement } from "react";
  export default function ReactECharts(props: { option: unknown; style?: CSSProperties; notMerge?: boolean }): ReactElement;
}
