import fs from "node:fs";
import * as echarts from "echarts";
import {chromium} from "playwright";
const request = JSON.parse(fs.readFileSync("/input/request.json", "utf8"));
const columns = request.data.columns, rows = request.data.rows, xIndex = columns.indexOf(request.x);
const series = request.y.map((name) => ({name, type: request.chart_type === "scatter" ? "scatter" : request.chart_type, data: rows.map((row) => [row[xIndex], row[columns.indexOf(name)]])}));
const option = {title: {text: request.title || ""}, tooltip: {}, xAxis: {}, yAxis: {}, series};
const chart = echarts.init(null, null, {renderer: "svg", ssr: true, width: request.width, height: request.height});
chart.setOption(option);
const svg = chart.renderToSVGString();
if (request.outputs.includes("svg")) fs.writeFileSync("/output/chart.svg", svg);
const escaped = JSON.stringify(option).replaceAll("<", "\\u003c");
const library = fs.readFileSync("/opt/astra/runtime/node_modules/echarts/dist/echarts.min.js", "utf8");
const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-astra-chart'; style-src 'unsafe-inline'; img-src data:"><style>html,body,#chart{margin:0;width:100%;height:100%}</style><div id="chart"></div><script nonce="astra-chart">${library}</script><script nonce="astra-chart">const chart=echarts.init(document.getElementById('chart'));chart.setOption(${escaped});</script>`;
if (request.outputs.includes("html")) fs.writeFileSync("/output/chart.html", html);
if (request.outputs.includes("png")) {
  const browser = await chromium.launch({headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"]});
  const page = await browser.newPage({viewport: {width: request.width, height: request.height}});
  await page.setContent(html, {waitUntil: "load"}); await page.screenshot({path: "/output/chart.png"}); await browser.close();
}
fs.writeFileSync("/output/chart-spec.json", JSON.stringify({...request, option}));
chart.dispose();
process.exit(0);
