import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {Workbook, FileBlob, SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
const parts=JSON.parse(await fs.readFile(path.join(dir,'coarse_type_payload.json'),'utf8'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const audit=[];
for(const p of parts){
  if(sha(await fs.readFile(p.desktop))!==p.source_sha256)throw Error(`${p.part}: desktop workbook changed after inspection`);
  if(sha(await fs.readFile(p.workspace))!==p.workspace_before_sha256)throw Error(`${p.part}: workspace workbook changed after inspection`);
  const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(p.desktop));
  const sheet=wb.worksheets.getItem('Sheet1');
  for(const c of p.changes){
    if(sheet.getRange(c.cell).values[0][0]!==c.from)throw Error(`${p.part}: unexpected type at ${c.cell}`);
    sheet.getRange(c.cell).values=[[c.to]];
  }
  // Import normalizes some view panes; restore the saved view explicitly.
  for(const [name,prior] of Object.entries(p.tables)){
    const sh=wb.worksheets.getItem(name);
    if(prior.freeze){
      const match=/^A(\d+)$/.exec(prior.freeze);
      if(!match)throw Error(`Unsupported pane to preserve: ${prior.freeze}`);
      sh.freezePanes.freezeRows(Number(match[1])-1);
    }else sh.freezePanes.unfreeze();
  }
  wb.recalculate();
  const out=await SpreadsheetFile.exportXlsx(wb);
  const staged=path.join(dir,`${p.part}_coarse_types.xlsx`);
  await out.save(staged);
  const preview=await wb.render({sheetName:'Sheet1',range:'A1:F12',scale:1.3,format:'png'});
  await fs.writeFile(path.join(dir,`${p.part}_coarse_types_preview.png`),new Uint8Array(await preview.arrayBuffer()));
  audit.push({part:p.part,changed_rows:p.changes.length,operation_type_mapping:{CIRCLE_PUNCH:'PUNCH',IRREGULAR_PUNCH:'PUNCH'},feature_type_unchanged:true,staged,staged_sha256:sha(await fs.readFile(staged))});
}
await fs.writeFile(path.join(dir,'coarse_type_authoring.json'),JSON.stringify(audit,null,2));
console.log(JSON.stringify(audit));
