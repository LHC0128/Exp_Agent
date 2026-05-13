rm = pyvisa.ResourceManager()
DEVADDR = 'USB0::0xF4EC::0x1015::SDSEV82X900704::INSTR'
ds_addr = rm.open_resource(DEVADDR)  # usb接口
ds_addr.timeout = 10000
ds_addr.HORI_NUM = 10
tdiv_enum = [200e-12,500e-12, 1e-9,\
            2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9, \
            1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6, \
            1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3, \
            1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]

ds_addr.write(":TRIGger:RUN")   # 运行示波器

ds_addr.write(":CHANnel1:SWITch ON")
ds_addr.write(":CHANnel2:SWITch OFF")
ds_addr.write(":CHANnel3:SWITch OFF")
ds_addr.write(":CHANnel4:SWITch OFF")

# ds_addr.write(":CHANnel1:SCALe 2")
# ds_addr.write(":CHANnel1:OFFSet 0.0")
ds_addr.write(":CHANnel1:SCALe 1")
ds_addr.write(":CHANnel1:OFFSet 0.0")

timescale = 0.1
ds_addr.write(":TIMebase:SCALe %.4f"%timescale)

ds_addr.write(":ACQuire:MMANagement FSRate")
ds_addr.write(":ACQuire:SRATe 2.00E5")
print(ds_addr.query(":ACQuire:SRATe?"))

def main_desc(recv):
    param_addr_type={"data_bytes":[0x3c,"i"],
                    "point_num":[0x74,'i'],
                    "fp":[0x84,'i'],
                    "sp":[0x88,'i'],
                    "vdiv":[0x9c,'f'],
                    "offset":[0xa0,'f'],
                    "code":[0xa4,'f'],
                    "adc_bit":[0xac,'h'],
                    "interval":[0xb0,'f'],
                    "delay":[0xb4,'d'],
                    "tdiv":[0x144,'h'],
                    "probe":[0x148,'f']}
    data_byte = {"i": 4, "f": 4, "h": 2, "d": 8}
    param_val ={}
    for key,addr_type in param_addr_type.items():
        addr_start = addr_type[0]
        format = addr_type[1]
        bytes = recv[addr_start:addr_start+data_byte[format]]
        param_val[key] = struct.unpack(format, bytes)[0]
    param_val["tdiv"] = tdiv_enum[param_val["tdiv"]]
    param_val["vdiv"] = param_val["vdiv"]*param_val["probe"]
    param_val["offset"] = param_val["offset"]*param_val["probe"]
    return param_val

def main_wf_data(sds, CHANNEL=1 , SampleF = 500000, SamplePoints = 200000, Triger = 0):
    for i in range(1,5):
        if i == CHANNEL:
            sds.write(f":CHANnel{i}:SWITch ON")
        else:
            sds.write(f":CHANnel{i}:SWITch OFF")
    if Triger > 0 :
        sds.write(f":CHANnel{Triger}:SWITch ON")
        sds.write(f":TRIGger:EDGE:SOURce C{Triger}")
    TempSwitch.wave_output_state(0)
    # time.sleep(3)
    # TempOff()
    sds.write(":TRIGger:RUN")
    time.sleep(0.1)
    sds.write(f":ACQuire:SRATe {SampleF}")
    timescale = SamplePoints/(SampleF*sds.HORI_NUM)
    sds.write(":TIMebase:SCALe %.4f"%timescale)
    if Triger > 0 :
        ds_addr.write(":TRIGger:MODE NORMal")
    else:
        ds_addr.write(":TRIGger:MODE FTRIG")
    sds.write(":TRIGger:STOP")
    time.sleep(0.2)

    sds.write(":TRIGger:RUN")
    time.sleep(timescale*20+0.5)
    sds.write(":TRIGger:STOP")
    TempSwitch.wave_output_state(1)
    # TempOn()
    sds.chunk_size = 20 * 1024 * 1024
    sds.write(f"WAV:SOUR C{CHANNEL}")
    sds.write("WAV:PREamble?")
    recv_all = sds.read_raw()
    recv = recv_all[recv_all.find(b'#') + 11:]
    param_dic = main_desc(recv)

    points = param_dic["point_num"]
    one_piece_num = float(sds.query(":WAVeform:MAXPoint?").strip())
    read_times = math.ceil(points / one_piece_num)

    if points > one_piece_num:
        sds.write(":WAVeform:POINt {}".format(one_piece_num))

    sds.write(":WAVeform:WIDTh WORD")

    recv_byte = b''
    for i in range(0, read_times):
        start = i * one_piece_num
        #Set the starting point of each slice
        sds.write(":WAVeform:STARt {}".format(start))
        sds.write("WAV:DATA?")
        time.sleep(1)
        recv_rtn = sds.read_raw()
        #Splice each waveform data based on data block information
        block_start = recv_rtn.find(b'#')
        data_digit = int(recv_rtn[block_start + 1:block_start + 2])
        data_start = block_start + 2 + data_digit
        data_len = int(recv_rtn[block_start + 2:data_start])
        recv_byte += recv_rtn[data_start:data_start + data_len]
    # print(f"实际接收数据点数: {len(recv_byte)//2}, 应接收数据点数: {points}")
    data_array = np.frombuffer(recv_byte, dtype=np.int16, count=points)
    
    volt_array = data_array / param_dic['code'] * param_dic['vdiv'] - param_dic['offset']
    
    time_array = np.arange(points, dtype=np.float64)  # 使用float64避免精度损失
    time_array = time_array * param_dic['interval'] + param_dic['delay']
    time_array -= param_dic['tdiv'] * sds.HORI_NUM / 2
    return volt_array, time_array