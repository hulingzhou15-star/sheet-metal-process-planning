import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const out=path.resolve(process.argv[2]);
const payload=JSON.parse(await fs.readFile(path.join(out,'plain_style_payload.json'),'utf8'));
const col=n=>{let s='';for(let v=n;v;v=Math.floor((v-1)/26))s=String.fromCharCode(65+(v-1)%26)+s;return s;};
const literal=v=>typeof v==='string' && v.startsWith('=') ? `'${v}` : v;
const checks=[];
for(const p of payload.parts) {
  const wb=Workbook.create();
  // Rebuild the explicitly requested plain five-sheet layout from the CURRENT
  // workbook snapshot. No project facts, source binding or expert answer is read.
  for(const name of ['Sheet1','Sheet2','Sheet3','Sheet4','Sheet5']) {
    const sheet=wb.worksheets.add(name), headers=p.headers[name], rows=p.records[name];
    const end=Math.max(rows.length+1,2), area=sheet.getRange(`A1:${col(headers.length)}${end}`);
    area.format.font={name:payload.style[name].font,size:payload.style[name].font_size,bold:false,color:'#000000'};
    area.format.rowHeight=payload.style[name].row_height;
    area.format.horizontalAlignment='center';
    area.format.verticalAlignment='center';
    area.format.wrapText=false;
    area.setNumberFormat('General');
    sheet.showGridLines=true;
    sheet.getRange(`A1:${col(headers.length)}1`).values=[headers];
    if(rows.length)sheet.getRange(`A2:${col(headers.length)}${end}`).values=rows.map(r=>headers.map(h=>literal(r[h]??null)));
    for(let i=1;i<=headers.length;i++){
      const letter=col(i);
      let width=payload.style[name].widths[letter]??8.72727272727273;
      if(name==='Sheet1' && headers[i-1]==='surface_side')width=Math.max(width,20);
      if(name==='Sheet4' && headers[i-1]==='fixed_station')width=Math.max(width,16);
      if(name==='Sheet5' && headers[i-1]==='influence_region_id')width=Math.max(width,21);
      if(name==='Sheet1' && i>10)width=({feature_type:31,geometry_shape:22,bend_angle_deg:20,protrusion_side:20,notes:42})[headers[i-1]];
      sheet.getRange(`${letter}1:${letter}${end}`).format.columnWidth=width;
    }
    for(const formula of p.formulas[name]){
      const c=headers.indexOf(formula.field)+1;
      sheet.getRange(`${col(c)}${formula.row}`).formulas=[[formula.formula]];
    }
    if(name==='Sheet1'||name==='Sheet2')sheet.freezePanes.freezeRows(1);
  }
  wb.recalculate();
  const scan=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:1800});
  await fs.writeFile(path.join(out,`${p.part}_plain_formula_scan.ndjson`),scan.ndjson);
  const inspect=await wb.inspect({kind:'region',sheetId:'Sheet1',range:'A1:J5',maxChars:1800,tableMaxRows:5,tableMaxCols:10});
  await fs.writeFile(path.join(out,`${p.part}_plain_inspection.ndjson`),inspect.ndjson);
  const target=path.join(out,`钣金加工数据表_${p.part}.xlsx`);
  await (await SpreadsheetFile.exportXlsx(wb)).save(target);
  for(const [name,range,suffix] of [['Sheet1','A1:J20','Sheet1'],['Sheet1','K1:O20','Sheet1_新增字段'],['Sheet2','A1:L6','Sheet2'],['Sheet3','A1:D7','Sheet3'],['Sheet4','A1:B7','Sheet4'],['Sheet5','A1:M10','Sheet5']]){
    const img=await wb.render({sheetName:name,range,scale:1.5,format:'png'});
    await fs.writeFile(path.join(out,`${p.part}_${suffix}_plain_preview.png`),new Uint8Array(await img.arrayBuffer()));
  }
  checks.push({part:p.part,rows:p.records.Sheet1.length,sheets:5,font:'Times New Roman',size:11,bold:false,table_objects:0,file:target});
  console.log(JSON.stringify(checks.at(-1)));
}
await fs.writeFile(path.join(out,'plain_authoring_checks.json'),JSON.stringify(checks,null,2));
