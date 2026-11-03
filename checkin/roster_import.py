"""Bounded CSV/XLSX decoding and explicit roster header mappings."""
import csv
import io
import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

STANDARD = {key: key for key in ('attendee_id', 'name', 'affiliation', 'table_no', 'grade', 'title', 'email', 'seating_area')}
CAMPUS = {'姓名': 'name', '年級': 'grade', '學號': 'attendee_id', '參加者編號': 'attendee_id',
          '職稱': 'title', '電子郵件地址': 'email', '電子郵件': 'email', 'Email': 'email',
          '單位': 'affiliation', '系所': 'affiliation', '區域': 'seating_area', '桌次': 'table_no'}


def cell_text(cell):
    value = cell.value
    if value is None:
        return ''
    if cell.data_type in ('f', 'e') or isinstance(value, bool):
        raise ValueError('名冊不可含公式、錯誤值或布林值，請貼上純文字資料。')
    if isinstance(value, (int, float)):
        if not float(value).is_integer() or abs(value) >= 10 ** 15:
            raise ValueError('數字欄位無法安全轉為編號，請將學號／編號設定為文字。')
        text = str(int(value))
        if re.fullmatch(r'0{2,20}', cell.number_format or ''):
            text = text.zfill(len(cell.number_format))
        return text
    if not isinstance(value, str):
        raise ValueError('名冊請使用文字，日期等欄位請先轉為文字。')
    return value


def excel_rows(raw):
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) > 500 or sum(entry.file_size for entry in entries) > 20 * 1024 * 1024
                or any(entry.flag_bits & 1 or entry.filename.lower().endswith('vbaproject.bin') for entry in entries)):
            raise ValueError('Excel 檔案內容過大、已加密或含巨集，請另存為單純的 .xlsx 名冊。')
    workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        selected = None
        for sheet in workbook.worksheets:
            if sheet.max_row and sheet.max_row > 1000 or sheet.max_column and sheet.max_column > 20:
                raise ValueError('Excel 工作表最多 1000 列、20 欄；請移除名冊以外的資料及多餘格式。')
            values = []
            for number, cells in enumerate(sheet.iter_rows(max_row=1000, max_col=min(sheet.max_column or 20, 20)), 1):
                if not any(cell.value is not None and str(cell.value).strip() for cell in cells):
                    continue
                try:
                    values.append((number, [cell_text(cell) for cell in cells]))
                except ValueError as error:
                    raise ValueError(f'第 {number} 列：{error}') from None
                if len(values) > 201:
                    raise ValueError('單次名冊不得超過 200 筆。')
            if values:
                if selected is not None:
                    raise ValueError('Excel 有多張非空白工作表，請只保留一張要匯入的名冊。')
                selected = values
        return selected or []
    finally:
        workbook.close()


def read_roster(raw, filename, profile='auto'):
    if profile not in ('auto', 'standard', 'campus'):
        return [], ['請選擇有效的匯入模板。']
    if len(raw) > 1024 * 1024:
        return [], ['檔案不得超過 1 MB。']
    try:
        extension = Path(filename or '').suffix.lower()
        if extension == '.xlsx':
            source = excel_rows(raw)
        elif extension == '.csv':
            reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
            source = [(number, row) for number, row in enumerate(reader, 1) if any(value.strip() for value in row)]
        else:
            raise ValueError('僅支援 UTF-8 CSV 或 Excel .xlsx；舊版 .xls 請先另存為 .xlsx。')
        if not source:
            raise ValueError('沒有可匯入的資料。')
        headers = [value.strip() for value in source[0][1]]
        while headers and not headers[-1]:
            headers.pop()
        selected = ('campus' if '姓名' in headers else 'standard') if profile == 'auto' else profile
        mapping = CAMPUS if selected == 'campus' else STANDARD
        fields = [mapping.get(header) for header in headers]
        if (not headers or None in fields or len(fields) != len(set(fields))
                or not {'attendee_id', 'name'}.issubset(fields)):
            raise ValueError('欄名不符或重複。校園模板需有「姓名、學號」，可選年級、職稱、電子郵件地址；一般模板需有 attendee_id、name。')
        if len(source) - 1 > 200:
            raise ValueError('單次名冊不得超過 200 筆。')
        result, errors = [], []
        for number, values in source[1:]:
            if len(values) > len(headers) and any(value.strip() for value in values[len(headers):]):
                errors.append(f'第 {number} 列：欄位數與標題不符。')
                continue
            if extension == '.csv' and len(values) != len(headers):
                errors.append(f'第 {number} 列：欄位數與標題不符。')
                continue
            values = (values + [''] * len(headers))[:len(headers)]
            item = dict(zip(fields, values))
            item['_profile'] = selected
            result.append((number, item))
        return result, errors
    except UnicodeDecodeError:
        return [], ['無法讀取檔案，請另存為 UTF-8 CSV。']
    except (BadZipFile, InvalidFileException, DefusedXmlException, ParseError, OSError, KeyError, TypeError, OverflowError):
        return [], ['無法讀取 Excel，請確認是有效、未加密的 .xlsx 檔案。']
    except (ValueError, csv.Error) as error:
        return [], [str(error) if isinstance(error, ValueError) else 'CSV 格式錯誤，請檢查引號及欄位。']
