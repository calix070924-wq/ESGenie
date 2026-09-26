// 읽기 전용 QA: 기존 production XLSX를 artifact-tool로 가져와 핵심 범위를 렌더링한다.
import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const reportPath=process.argv[2];
if(!reportPath) throw new Error('Usage: node hmc_render_excel.mjs validation_report.json');
const report=JSON.parse(await fs.readFile(reportPath,'utf8'));
const record=report.controlled;
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(record.files.xlsx.path));
const out=path.dirname(record.files.xlsx.path);
const ranges=[['header','A1:G5'],...Object.entries(record.xlsx_rows).map(([qid,row])=>[qid,`A${row}:G${row}`])];
const shots=[];
for(const [label,range] of ranges){
  const preview=await workbook.render({sheetName:'응답서',range,scale:1.2,format:'png'});
  const dest=path.join(out,`excel_${label}.png`);
  await fs.writeFile(dest,new Uint8Array(await preview.arrayBuffer()));
  shots.push({path:dest,sheet:'응답서',range});
}
const preview=await workbook.render({sheetName:'증빙 체크리스트',range:'A20:F27',scale:1.2,format:'png'});
const dest=path.join(out,'excel_checklist.png');
await fs.writeFile(dest,new Uint8Array(await preview.arrayBuffer()));
shots.push({path:dest,sheet:'증빙 체크리스트',range:'A20:F27'});
await fs.writeFile(path.join(path.dirname(reportPath),'excel_renders.json'),JSON.stringify({renderer:'@oai/artifact-tool importXlsx/render (read-only)',shots},null,2));
console.log(JSON.stringify(shots));
