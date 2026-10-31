"""只包含已分析的查詢、原廠使用者設定與即時燈光封包；不含 OTA、韌體或動畫快閃寫入。"""
import math
TABLES={0x10:48,0x11:56,0x12:512,0x15:5120,0x16:512,0x21:56,0x22:512,0x25:5120,0x26:512}
ALLOWED=set(TABLES)|{0x32,0x35,0x3c}

def header(cmd,data=b'',offset=0,last=True,size=64):
    if cmd not in set(TABLES)|{0x32,0x3c}:raise ValueError('不允許此命令')
    if len(data)>size-8 or offset<0 or offset>65535:raise ValueError('無效長度')
    return bytes((0xaa,cmd,len(data),offset&255,offset>>8,0,int(last),0))+data+bytes(size-8-len(data))

def query_info(size,offset=0,count=48):
    if size not in (32,64) or not 0<=offset<48:raise ValueError('無效查詢')
    n=min(count,size-8,48-offset)
    return bytes((0xaa,0x10,n,offset&255,offset>>8,0,int(offset+n>=48),0))+bytes(size-8)

def rgb565(rgb):
    r,g,b=rgb;v=((r&248)<<8)|((g&252)<<3)|((b&248)>>3)
    return bytes((v>>8,v&255))

def frames(colors,order,mode='USB'):
    if len(order)!=90 or len(set(order))!=90:raise ValueError('必須提供完整 90 鍵順序')
    for k in order:
        if len(colors[k])!=3 or any(type(x)is not int or not 0<=x<=255 for x in colors[k]):raise ValueError('無效 RGB')
    if mode=='USB':
        data=b''.join(bytes((k,*colors[k])) for k in order)
        return [header(0x32,data[i:i+56],i,i+56>=len(data)) for i in range(0,len(data),56)]
    data=b''.join(rgb565(colors[k]) for k in order)
    if mode=='2.4G':
        # 官方 uu() 的 legacy 分支，32-byte / 4-byte 自訂 header，RGB565 big endian。
        return [bytes((0xaa,0x35,i//28,0))+data[i:i+28]+bytes(28-len(data[i:i+28])) for i in range(0,len(data),28)]
    if mode=='2.4G-v1':
        # 只供 lightingVersion=1 且實際 64-byte FF70 介面的裝置。
        return [header(0x3c,data[i:i+56],i,i+56>=len(data)) for i in range(0,len(data),56)]
    raise ValueError('未知連線類型')

def check_report(report):
    if len(report) not in (32,64) or report[0]!=0xaa or report[1] not in ALLOWED:raise ValueError('拒絕未允許的封包')
    if report[1] in TABLES:
        end=int.from_bytes(report[3:5],'little')+report[2]
        if report[2]>len(report)-8 or end>TABLES[report[1]]:raise ValueError('表格存取超出範圍')


def query_table(cmd,size,offset,length,last=True):
    if cmd not in (0x11,0x12,0x15,0x16) or not 0<length<=size-8 or offset+length>TABLES[cmd]:raise ValueError('查詢範圍無效')
    return bytes((0xaa,cmd,length,offset&255,offset>>8,0,int(last),0))+bytes(size-8)

def write_table(cmd,data,size,offset=0,final=True):
    if cmd not in (0x21,0x22,0x25,0x26) or offset+len(data)>TABLES[cmd]:raise ValueError('設定寫入超出範圍')
    n=size-8
    return [header(cmd,data[i:i+n],offset+i,final and i+n>=len(data),size)for i in range(0,len(data),n)]
