import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';

const out=path.resolve(process.argv[2]);
const payload=JSON.parse(await fs.readFile(path.join(out,'geometry_payload.json'),'utf8'));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const col=n=>{let s='';for(let v=n;v;v=Math.floor((v-1)/26))s=String.fromCharCode(65+(v-1)%26)+s;return s;};
const audit=[];
for(const p of payload){
  if(hash(await fs.readFile(p.desktop))!==p.desktop_sha256)throw Error(`${p.part}: desktop input changed`);
  const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(p.desktop));
  const s1=wb.worksheets.getItem('Sheet1'),s2=wb.worksheets.getItem('Sheet2');
  const originalColumns=p.sheet1_headers.length;
  const headers=[...p.sheet1_headers,'geometry_status','geometry_note'];
  s1.getRange(`A1:${col(headers.length)}1`).values=[headers];
  const n=p.tables.Sheet1.max_row;
  const metadata=s1.getRange(`${col(originalColumns+1)}1:${col(headers.length)}${n}`);
  metadata.format.font={name:'Times New Roman',size:11,bold:false,color:'#000000'};
  metadata.format.horizontalAlignment='center';
  metadata.format.verticalAlignment='center';
  metadata.format.wrapText=false;
  s1.getRange(`${col(originalColumns+1)}1:${col(originalColumns+1)}${n}`).format.columnWidth=31;
  s1.getRange(`${col(originalColumns+2)}1:${col(originalColumns+2)}${n}`).format.columnWidth=96;
  const geomColumn=p.sheet1_headers.indexOf('geometry_id')+1;
  for(const u of p.updates){
    const cell=s1.getRange(`${col(geomColumn)}${u.excel_row}`);
    if(cell.values[0][0]!==null && cell.values[0][0]!=='')throw Error(`${p.part}/${u.operation_id}: existing geometry identity`);
    cell.values=[[u.geometry_id]];
    s1.getRange(`${col(originalColumns+1)}${u.excel_row}:${col(originalColumns+2)}${u.excel_row}`).values=[[u.geometry_status,u.geometry_note]];
  }
  const length=p.sheet2_rows.length+1,columns=p.sheet2_headers.length;
  const all=s2.getRange(`A1:${col(columns)}${length}`);
  all.format.font={name:'Times New Roman',size:11,bold:false,color:'#000000'};
  all.format.horizontalAlignment='center';
  all.format.verticalAlignment='center';
  all.format.rowHeight=18;
  all.format.wrapText=false;
  s2.showGridLines=true;
  s2.getRange(`A1:${col(columns)}1`).values=[p.sheet2_headers];
  s2.getRange(`A2:${col(columns)}${length}`).values=p.sheet2_rows;
  s2.getRange(`C2:C${length}`).setNumberFormat('0');
  s2.getRange(`E2:L${length}`).setNumberFormat('0.000000');
  const widths=[12,14,8,16,18,18,18,18,18,18,16,18,16,18,39,31,55,18];
  widths.forEach((v,i)=>s2.getRange(`${col(i+1)}1:${col(i+1)}${length}`).format.columnWidth=v);
  // The affected Sheet2 now scrolls as a data table with one frozen header.
  for(const [name,t] of Object.entries(p.tables)){
    const sh=wb.worksheets.getItem(name);
    if(name==='Sheet2')sh.freezePanes.freezeRows(1);
    else if(t.freeze){
      const m=/^A(\d+)$/.exec(t.freeze);
      if(!m)throw Error(`Unsupported existing pane: ${t.freeze}`);
      sh.freezePanes.freezeRows(Number(m[1])-1);
    }else sh.freezePanes.unfreeze();
  }
  wb.recalculate();
  const target=path.join(out,`钣金加工数据表_${p.part}.xlsx`);
  await(await SpreadsheetFile.exportXlsx(wb)).save(target);
  const inspect=await wb.inspect({kind:'region',sheetId:'Sheet2',range:'A1:L8',maxChars:2300,tableMaxRows:8,tableMaxCols:12});
  await fs.writeFile(path.join(out,`${p.part}_sheet2_inspection.ndjson`),inspect.ndjson);
  const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:1800});
  await fs.writeFile(path.join(out,`${p.part}_formula_scan.ndjson`),errors.ndjson);
  for(const [sheet,range,name] of [['Sheet2','A1:L12','Sheet2'],['Sheet2','M1:R10','Sheet2_来源状态'],['Sheet1','J1:Q10','Sheet1_几何关联']]){
    const im=await wb.render({sheetName:sheet,range,scale:1.3,format:'png'});
    await fs.writeFile(path.join(out,`${p.part}_${name}_preview.png`),new Uint8Array(await im.arrayBuffer()));
  }
  audit.push({part:p.part,output:target,geometry_count:p.updates.length,geometry_rows:p.sheet2_rows.length,status_counts:p.status_counts,sha256:hash(await fs.readFile(target))});
  console.log(JSON.stringify(audit.at(-1)));
}
await fs.writeFile(path.join(out,'authoring_checks.json'),JSON.stringify(audit,null,2));
