"""Portable XLSX export without additional packages on the user's server."""
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo


def _column(index):
    result=''
    while index:
        index,remainder=divmod(index-1,26)
        result=chr(65+remainder)+result
    return result


def _cell(value, address, header=False):
    if value is None:
        return f'<c r="{address}"/>'
    if isinstance(value,datetime):
        local=value.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None) if value.tzinfo else value
        serial=(local-datetime(1899,12,30)).total_seconds()/86400
        return f'<c r="{address}" s="2"><v>{serial}</v></c>'
    if isinstance(value,(int,float,Decimal)):
        return f'<c r="{address}"><v>{value}</v></c>'
    # Inline strings cannot become formulas, including names beginning with '='.
    text=''.join(c for c in str(value) if ord(c)>=32 or c in '\t\n\r')[:32767]
    return f'<c r="{address}" t="inlineStr" s="{1 if header else 0}"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def student_workbook(students, attempts):
    subject={'history':'История','society':'Обществознание'}
    status={'active':'Активен','blocked':'Заблокирован','completed':'Завершена','in_progress':'В процессе','abandoned':'Прервана','expired':'Истекло время'}
    sheets=[('Ученики',[
      ['ID','Имя','Почта','Доступ','Предметы','Группы','Регистрация (МСК)','Активность (МСК)','Тренировка (МСК)','Заметки'],
      *[[r['id'],r['name'],r['email'],status.get(r['status'],r['status']),', '.join(subject[x] for x in r['subjects']),', '.join(r['groups']),r['registeredAt'],r['lastActivity'],r['lastPractice'],r['notes']] for r in students]]),
      ('Тренировки',[
      ['ID ученика','Имя','Почта','Тренировка','Предмет','Статус','Начало (МСК)','Завершение (МСК)','Баллы','Максимум','Верных ответов','Вопросов'],
      *[[r['user_id'],r['name'],r['email'],r['title'],subject.get(r['subject'],r['subject']),status.get(r['status'],r['status']),r['started_at'],r['completed_at'],r['score'],r['max_score'],r['correct_count'],r['question_count']] for r in attempts]])]
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,3))+'</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,3))+'<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml','''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="dd.mm.yyyy hh:mm"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF696DCE"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        for i,(_,rows) in enumerate(sheets,1):
            if len(rows)>1048576:
                raise ValueError('Слишком много строк для Excel')
            last=_column(len(rows[0]))
            xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="12" width="24" customWidth="1"/></cols><sheetData>'
            for n,row in enumerate(rows,1):
                xml+=f'<row r="{n}">'+''.join(_cell(v,f'{_column(j)}{n}',n==1) for j,v in enumerate(row,1))+'</row>'
            z.writestr(f'xl/worksheets/sheet{i}.xml',xml+f'</sheetData><autoFilter ref="A1:{last}{len(rows)}"/></worksheet>')
    return out.getvalue()
