import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const out=path.resolve(process.argv[2]);
const payload=JSON.parse(await fs.readFile(path.join(out,'review_payload.json'),'utf8'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const audit=[];
for(const p of payload.parts){
  if(sha(await fs.readFile(p.desktop))!==p.desktop_sha256)throw Error(`${p.part}: source workbook changed`);
  const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(p.desktop));
  const s1=wb.worksheets.getItem('Sheet1'),s2=wb.worksheets.getItem('Sheet2');
  for(const [cell,change] of Object.entries(p.changes)){
    if(s1.getRange(cell).values[0][0]!==change.before)throw Error(`Cell ${cell} differs from inspected data`);
    s1.getRange(cell).values=[[change.after]];
  }
  const length=p.sheet2_rows.length+1;
  s2.getRange(`A2:R${length}`).values=p.sheet2_rows;
  const last=s2.getRange(`A${length-1}:R${length}`);
  last.format.font={name:'Times New Roman',size:11,bold:false,color:'#000000'};
  last.format.horizontalAlignment='center';last.format.verticalAlignment='center';last.format.rowHeight=18;
  s2.getRange(`E2:L${length}`).setNumberFormat('0.000000');
  s2.getRange(`C2:C${length}`).setNumberFormat('0');
  for(const [name,t] of Object.entries(p.tables)){
    const sheet=wb.worksheets.getItem(name);
    if(t.freeze){const m=/^A(\d+)$/.exec(t.freeze);if(!m)throw Error('Unsupported pane');sheet.freezePanes.freezeRows(Number(m[1])-1);}
    else sheet.freezePanes.unfreeze();
  }
  wb.recalculate();
  const target=path.join(out,`钣金加工数据表_${p.part}.xlsx`);
  await(await SpreadsheetFile.exportXlsx(wb)).save(target);
  const operation=p.part==='P002'?'O069':'O112';
  const start=p.sheet2_rows.findIndex(r=>r[12]===operation)+2;
  for(const [sheet,range,label] of [['Sheet2',`A${start-1}:L${start+16}`,'geometry'],['Sheet1',p.part==='P002'?'K120:Q122':'K112:Q114','review']]){
    const img=await wb.render({sheetName:sheet,range,scale:1.2,format:'png'});
    await fs.writeFile(path.join(out,`${p.part}_${label}_preview.png`),new Uint8Array(await img.arrayBuffer()));
  }
  audit.push({part:p.part,output:target,sheet2_rows:p.sheet2_rows.length,sha256:sha(await fs.readFile(target))});
  console.log(JSON.stringify(audit.at(-1)));
}
await fs.writeFile(path.join(out,'authoring_checks.json'),JSON.stringify(audit,null,2));
