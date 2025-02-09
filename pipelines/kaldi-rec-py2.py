#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Overall settings and paths to data files
#rootdir = '/appl/ling/kaldi-asr/1.0/build/'
rootdir = '/opt/kaldi/egs/kaldi-rec/s5/'
import argparse
import math
import os
import sys
import struct
import fileinput
from lxml import etree
from datetime import datetime
from mimetypes import guess_type
import argparse
import random
import string
import subprocess
from subprocess import PIPE
import glob

class ModelAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == 'list':
            sys.stderr.write('supported models:\n')
            for m in sorted(asr_models.keys()):
                if 'default' in asr_models[m].keys():
                    sys.stderr.write('%s: %s [sample rate: %d Hz] (default)\n' % (m,asr_models[m]['lang'], asr_models[m]['srate']))
                else:
                    sys.stderr.write('%s: %s [sample rate: %d Hz]\n' % (m,asr_models[m]['lang'], asr_models[m]['srate']))
            sys.exit(2)
        setattr(namespace, self.dest, values)


def Aalto_speaker_segment_audio(SOX_PATH,srate,infile):
    diarization_filename = infile.replace(".wav",".seg")
    #Create temp dir
    file_path = ""
    file_id = ""
    if "/" in infile:
        file_path,file_id = infile.rsplit("/",1)
        file_path += "/"
        if "." in file_id:
            file_id,ext = file_id.rsplit(".",1)
    else:
        if "." in infile:
            file_id,ext = infile.rsplit(".",1)
        else:
            file_id = infile

    temp_diarization_dir = file_path+"aalto_diarization_"+file_id
    #check if kaldi_data_dir exists
    if os.path.isdir(temp_diarization_dir) == False:
        os.mkdir(temp_diarization_dir)

    diarization_filename = infile.replace(".wav",".aalto.diarization.recipe")
    aalto_diarization_script = rootdir+"/aalto-speaker-diarization/spk-diarization.py"
    aalto_bin_dir = rootdir+"/aalto-speaker-diarization/bin"
    hmm_model = rootdir+"/aalto-speaker-diarization/hmms/mfcc_16g_9.10.2007_10.cfg"
    os.system(aalto_diarization_script+" -fc "+aalto_bin_dir+" -fcfg "+hmm_model+" -o "+diarization_filename+" -lna "+temp_diarization_dir+" -exp "+temp_diarization_dir+" -fp "+temp_diarization_dir+" -tmp "+temp_diarization_dir+" "+infile)

    diarization_file = open(diarization_filename,"r")
    # generate the resulting audio files
    audiofiles = []
    seg_index = 1
    for line in diarization_file:
        line = line.strip()
        audio,lna,start_time,end_time,speaker_id =line.split(" ",4)
        start_seconds = float(start_time.replace("start-time=",""))
        end_seconds = float(end_time.replace("end-time=",""))
        dur_seconds = end_seconds-start_seconds
        speaker_id = speaker_id.replace("speaker=","")
        str_seg_index = '{:06}'.format(seg_index)
        seg_filename = infile.replace(".wav","_"+str_seg_index+".wav")
        os.system(SOX_PATH+" "+infile+" -t wav -r "+str(srate)+" -b 16 -e signed-integer -c 1 "+seg_filename+" trim "+str(start_seconds)+" "+str(dur_seconds))
        audiofiles.append({ 'start': start_seconds, 'file': seg_filename , 'speaker':speaker_id})
        seg_index += 1
    diarization_file.close()
    #Remove temp files
    os.remove(diarization_filename)
    os.system("rm -rf "+temp_diarization_dir+"/*")
    os.system("rmdir "+temp_diarization_dir)
    return audiofiles

def LIUM_speaker_segment_audio(SOX_PATH,srate,infile):
    diarization_filename = infile.replace(".wav",".seg")
    file_id = infile.replace(".wav","")
    LIUM_model = rootdir+"/LIUM/LIUM_SpkDiarization-8.4.1.jar"
    os.system("java -Xmx2024m -jar "+LIUM_model+" --fInputMask="+infile+" --sOutputMask="+diarization_filename+" --doCEClustering "+file_id)
    diarization_file = open(diarization_filename,"r")
    # generate the resulting audio files
    audiofiles = []
    seg_index = 1
    for line in diarization_file:
        line = line.strip()
        if line.startswith(";") == False:
            file_id,ext1,start_features,dur_features,gender,ext2,ext3,speaker_id =line.split(" ",7)
            start_seconds = float(1.0*int(start_features)/100.0)
            dur_seconds = float(1.0*int(dur_features)/100.0)
            str_seg_index = '{:06}'.format(seg_index)
            seg_filename = infile.replace(".wav","_"+str_seg_index+".wav")
            os.system(SOX_PATH+" "+infile+" -t wav -r "+str(srate)+" -b 16 -e signed-integer -c 1 "+seg_filename+" trim "+str(start_seconds)+" "+str(dur_seconds))
            audiofiles.append({ 'start': start_seconds, 'file': seg_filename , 'speaker':speaker_id})
            seg_index += 1
    diarization_file.close()
    os.remove(diarization_filename)
    return audiofiles


def split_audio(SOX_PATH,srate,infile):
    """Split an input audio file to approximately seglen-second segments,
    at more or less silent positions if possible.  Frame size used when
    splitting will match the frame size of the model, and the output list
    gives start offsets of the segments in terms of that.
    """
    # compute target segment length in frames
    framesize = 125
    seglen = 20
    segframes = int(seglen * srate / framesize)
    max_offset = segframes / 5

    # generate frame energy mapping for the input audio
    raw_filename = infile+".raw"
    os.system(SOX_PATH+" "+infile+" -t raw -r "+str(srate)+" -b 16 -e signed-integer -c 1 "+raw_filename)
    raw_file = open(raw_filename,"rb")
    raw_energy = []
    while True:
        frame = raw_file.read(2*framesize)
        if len(frame) < 2*framesize:
            break
        frame = struct.unpack('={0}h'.format(framesize), frame)
        mean = float(sum(frame)) / len(frame)
        raw_energy.append(math.sqrt(sum((s-mean)**2 for s in frame)))
    raw_file.close()
    os.remove(raw_filename)
    if not raw_energy:
        err("input conversion of '{0}' with sox resulted in no frames".format(infile), exit=1)

    # moving-average smoothing for the energy
    energy = [0.0]*len(raw_energy)

    for i in range(len(energy)):
        wnd = raw_energy[max(i-10,0):i+11]
        energy[i] = sum(wnd)/len(wnd)

    # determine splitting positions

    segments = []
    at = 0

    while at < len(energy):
        left = len(energy) - at

        if left <= 1.5 * segframes:
            take = left
        else:
            target = at + segframes
            minpos = max(0, int(target - max_offset))
            maxpos = min(len(energy), int(target + max_offset + 1))
            pos = minpos + min(enumerate(energy[minpos:maxpos]),
                               key=lambda v: (1+abs(minpos+v[0]-target)/max_offset)*v[1])[0]
            take = pos - at

        segments.append((at, at+take))
        at += take
    # generate the resulting audio files
    audiofiles = []
    seg_index = 1
    for i, (start, end) in enumerate(segments):
        starts = start*framesize
        lens = (end-start)*framesize

        start_seconds = float(1.0*starts/srate)
        dur_seconds = float(1.0*lens/srate)

        seg_filename = infile.replace(".wav","_"+str(seg_index)+".wav")

        os.system(SOX_PATH+" "+infile+" -t wav -r "+str(srate)+" -b 16 -e signed-integer -c 1 "+seg_filename+" trim "+str(start_seconds)+" "+str(dur_seconds))
        audiofiles.append({ 'start': start_seconds, 'file': seg_filename, 'speaker': 'S1' })
        seg_index += 1
    return audiofiles


#return audio length in seconds
def audio_file_len(audio_filename):
    ms = 0
    audiofile = open(audio_filename,"r")
    audiofile.seek(28)
    a=audiofile.read(4)

    #convert string a into integer/longint value
    #a is little endian, so proper conversion is required
    byteRate=0
    for i in range(4):
        byteRate=byteRate + ord(a[i])*pow(256,i)
    #get the file size in bytes
    fileSize=os.path.getsize(filename)
    #the duration of the data, in milliseconds, is given by
    if (byteRate+ms) > 0:
        try:
            ms=((fileSize-44)*1000)/byteRate+ms
        except:
            pass
    audiofile.close()

    audio_file_len = float(ms*1.0/1000.0)
    return audio_file_len

def create_data_folder(target_wav_filename,seg_filenames):
    file_path = ""
    file_id = ""
    if "/" in target_wav_filename:
        file_path,file_id = target_wav_filename.rsplit("/",1)
        file_path += "/"
        if "." in file_id:
            file_id,ext = file_id.rsplit(".",1)
    else:
        if "." in target_wav_filename:
            file_id,ext = target_wav_filename.rsplit(".",1)
        else:
            file_id = target_wav_filename

    kaldi_data_dir = file_path+"kaldi_data_"+file_id
    #check if kaldi_data_dir exists
    if os.path.isdir(kaldi_data_dir) == False:
        os.mkdir(kaldi_data_dir)
    #wav.scp
    wav_scp_filename = kaldi_data_dir+"/wav.scp"
    wav_scp_file = open(wav_scp_filename,"w")
    audio_filenames = []
    for seg in seg_filenames:
        audio_filename = seg['file']
        audio_filenames.append(audio_filename)
        #file_id,ext= audio_filename.rsplit(".",1)
        #ext,file_id = file_id.rsplit("/",1)
        #file_id = file_id.strip()
        file_id = get_file_id(audio_filename)
        wav_scp_file.write(file_id+" "+audio_filename+"\n")
    wav_scp_file.close()

    #wav-list
    wav_list_filename = kaldi_data_dir+"/wav-list"
    wav_list_file = open(wav_list_filename,"w")
    for audio_filename in audio_filenames:
        wav_list_file.write(audio_filename+"\n")
    wav_list_file.close()

    #verbatim.ref
    transcript = "NULL"
    verbatim_filename = kaldi_data_dir+"/verbatim.ref"
    verbatim_file = open(verbatim_filename,"w")
    for audio_filename in audio_filenames:
        #file_id,ext= audio_filename.rsplit(".",1)
        #ext,file_id = file_id.rsplit("/",1)
        #file_id = file_id.strip()
        file_id = get_file_id(audio_filename)
        verbatim_file.write(file_id+" "+transcript+"\n")
    verbatim_file.close()
    text_filename = verbatim_filename.replace("verbatim.ref","text")
    os.system("cp "+verbatim_filename+" "+text_filename)

    #spk2utt
    spk2utt = dict()
    for seg in seg_filenames:
        audio_filename = seg['file']
        speaker = seg['speaker']
        #file_id,ext= audio_filename.rsplit(".",1)
        #ext,file_id = file_id.rsplit("/",1)
        #file_id = file_id.strip()
        file_id = get_file_id(audio_filename)
        if speaker in spk2utt:
            spk2utt[speaker] += file_id+" "
        else:
            spk2utt[speaker] = file_id+" "
    spk2utt_filename = kaldi_data_dir+"/spk2utt"
    spk2utt_file = open(spk2utt_filename,"w")
    for speaker,utt in spk2utt.iteritems():
        spk2utt_file.write(speaker+" "+utt.strip()+"\n")
    spk2utt_file.close()

    #utt2spk
    utt2spk_filename = kaldi_data_dir+"/utt2spk"
    utt2spk_file = open(utt2spk_filename,"w")
    recipe = sys.argv[1].strip()
    for seg in seg_filenames:
        audio_filename = seg['file']
        speaker = seg['speaker']
        #file_id,ext= audio_filename.rsplit(".",1)
        #ext,file_id = file_id.rsplit("/",1)
        #file_id = file_id.strip()
        file_id = get_file_id(audio_filename)
        utt2spk_file.write(file_id+" "+speaker+"\n")
    utt2spk_file.close()

    #plain.txt
    plain_filename = kaldi_data_dir+"/plain.txt"
    plain_file = open(plain_filename,"w")
    for line in audio_filenames:
        plain_file.write(transcript+"\n")
    plain_file.close()

    return kaldi_data_dir

def remove_temp_files(target_wav_filename,seg_filenames,data_folder):
    os.remove(target_wav_filename)
    #audiofiles
    for seg in seg_filenames:
        audio_filename = seg['file']
        os.remove(audio_filename)
    #data dir
    os.system("rm -rf "+data_folder+"/*")
    os.system("rm -rf "+data_folder+"/.backup*")
    os.system("rmdir "+data_folder)

def dateIso():
    """ Returns the actual date in the format expected by ELAN. Source:
        http://stackoverflow.com/questions/3401428/how-to-get-an-isoformat-datetime-string-including-the-default-timezone"""
    dtnow = datetime.now()
    dtutcnow = datetime.utcnow()
    delta = dtnow - dtutcnow
    hh, mm = divmod((delta.days * 24 * 60 * 60 + delta.seconds + 30) // 60, 60)
    return '%s%+02d:%02d' % (dtnow.isoformat(), hh, mm)


def alignment_to_milliseconds(rfile):
    """Parses input mseg file"""
    r = []
    index = 0
    prev_token = ""
    for line in rfile:
        start_s = line[0]
        end_s = line[1]
        token = line[2]
        speaker_id = line[3]
        start_ms = int(float(start_s) * 1000.0)
        end_ms = int(float(end_s) * 1000.0)
        r.append((start_ms,end_ms,token,speaker_id))
    return r


def write_elan(media_file,rfile,outf):
    print "Writing ELAN"
    print media_file
    print guess_type(media_file)[0]
    media_type = guess_type(media_file)[0]
    if media_type == None:
        media_type = ''
    """Write Elan file"""
    ts_count = 1
    an_count = 1
    NS = 'http://www.w3.org/2001/XMLSchema-instance'
    location_attr = '{%s}noNamespaceSchemaLocation' % NS
    doc = etree.Element('ANNOTATION_DOCUMENT',
                        attrib={location_attr: 'http://www.mpi.nl/tools/elan/EAFv2.7.xsd',
                                'AUTHOR': '', 'DATE': dateIso(),
                                'FORMAT': '2.7', 'VERSION': '2.7'})
    header = etree.SubElement(doc, 'HEADER',
                              attrib={'MEDIA_FILE': '',
                                      'TIME_UNITS': 'milliseconds'})
    etree.SubElement(header, 'MEDIA_DESCRIPTOR',
                     attrib={'MEDIA_URL': media_file,
                             'MIME_TYPE': media_type,
                             'RELATIVE_MEDIA_URL': ''})
    t = etree.SubElement(header, 'PROPERTY',
                         attrib={'NAME': 'lastUsedAnnotationId'})
    t.text = str(len(rfile))
    time = etree.SubElement(doc, 'TIME_ORDER')
    for line in rfile:
        start = str(line[0])
        end = str(line[1])
        token = str(line[2])
        speaker_id = str(line[3])
        #start,end,token,speaker_id = line.split("\t",3)
        etree.SubElement(time, 'TIME_SLOT',
                         attrib={'TIME_SLOT_ID': 'ts' + str(ts_count),
                                 'TIME_VALUE': start})
        ts_count += 1
        etree.SubElement(time, 'TIME_SLOT',
                         attrib={'TIME_SLOT_ID': 'ts' + str(ts_count),
                                 'TIME_VALUE': end})
        ts_count += 1

    #tier = etree.SubElement(doc, 'TIER',attrib={'DEFAULT_LOCALE': 'fi','LINGUISTIC_TYPE_REF': 'default-lt','TIER_ID': 'Speakers'})
    ts_count = 1
    index = 0
    current_speaker = ""
    seg_count = 1
    speakers = []
    for line in rfile:
        start = str(line[0])
        end = str(line[1])
        token = str(line[2])
        speaker_id = str(line[3])
        #start,end,token,speaker_id = line.split("\t",3)
        if speaker_id != current_speaker:
            #speaker_utf8 = speaker_id.decode('iso-8859-15')
            speaker_utf8 = speaker_id
            speaker_utf8 = speaker_utf8.replace(":","")
            tier_id = speaker_utf8.strip()+" "+str(seg_count)
            #tier_id = speaker_utf8.strip()
            speakers.append(speaker_utf8.strip())
            if speaker_utf8.strip() == "Puhuja":
                speaker_utf8 = speakers[len(speakers)-3]
                tier_id = speaker_utf8.strip()+" "+str(seg_count)
                #tier_id = speaker_utf8.strip()
            #tier = etree.SubElement(doc, 'TIER',attrib={'DEFAULT_LOCALE': 'fi','LINGUISTIC_TYPE_REF': 'default-lt','TIER_ID': unicode(tier_id),'PARTICIPANT':unicode(speaker_utf8)})
            tier = etree.SubElement(doc, 'TIER',attrib={'DEFAULT_LOCALE': 'fi','LINGUISTIC_TYPE_REF': 'default-lt','TIER_ID': tier_id,'PARTICIPANT':speaker_utf8})
            current_speaker = speaker_id
            seg_count += 1

        a = etree.SubElement(tier, 'ANNOTATION')
        aa = etree.SubElement(a, 'ALIGNABLE_ANNOTATION',
                         attrib={'ANNOTATION_ID': 'a' + str(an_count),
                                 'TIME_SLOT_REF1': 'ts' + str(ts_count),
                                 'TIME_SLOT_REF2': 'ts' + str(ts_count + 1)})
        token = token.decode('utf-8')
        av = etree.SubElement(aa,'ANNOTATION_VALUE')
        av.text = token
        an_count += 1
        ts_count += 2
        index += 1
    etree.SubElement(doc, 'LINGUISTIC_TYPE',
                     attrib={'GRAPHIC_REFERENCES': 'false',
                             'LINGUISTIC_TYPE_ID': 'default-lt',
                             'TIME_ALIGNABLE': 'true'})
    etree.SubElement(doc, 'LOCALE',
                     attrib={'COUNTRY_CODE': 'FI',
                             'LANGUAGE_CODE': 'fi'})
    tree = etree.ElementTree(doc)
    tree.write(outf, pretty_print=True,encoding="utf-8")


def rotate(l, n):
    return l[n:] + l[:n]

def get_unformatted_string(formatted_subtitle_string):
    unformatted_subtitle_string = ""
    if "<" in formatted_subtitle_string:
        sub_strings = formatted_subtitle_string.split("<")
        for sub_string in sub_strings:
            if len(sub_string) > 0:
                ext,text_string = sub_string.split(">")
                unformatted_subtitle_string += text_string
    else:
        unformatted_subtitle_string = formatted_subtitle_string

    return unformatted_subtitle_string

def write_srt(asr_segmentations,srt_filename):
    MAX_COLUMN = 36
    word_segs = []
    for seg in asr_segmentations:
        start = str(seg[0])
        end = str(seg[1])
        token = str(seg[2])
        speaker_id = str(seg[3])
        out_seg = start+"\t"+end+"\t"+token+"\t"+speaker_id
        if len(token.strip()) > 0:
            word_segs.append(out_seg)

    word_segs_filtered = []
    index = 0
    prev_word = ""
    for line in word_segs:
        try:
            next_line = word_segs[index+1]
            next_start,next_end,next_word,next_speaker = next_line.split("\t")
        except:
            next_start = start.strip()
            next_end = end.strip()
            next_word = word.strip()
        start,end,word,speaker = line.split("\t")
        word = word.strip()

        if word == ".":
            if prev_word == ".":
                pass
            else:
                word_segs_filtered.append(line)
        else:
            word_segs_filtered.append(line)
        prev_word = word
        index += 1

    index = 0
    subtitle = ""
    insert_index = 1
    sentence_start_time = 0.0
    srt_file = open(srt_filename,"w")
    alternating_sub_colors = ["<font color=\"#ffff00\">","<font color=\"#ffffff\">"]
    for line in word_segs_filtered:
        start,end,word,speaker = line.split("\t")
        word = word.strip()
        if len(word) != 0:
            if len(subtitle) == 0 and index != 0:
                sentence_start_time = start
            if index != 0:
                prev_line = word_segs_filtered[index-1]
                prev_start,prev_end,prev_word,prev_speaker = prev_line.split("\t")
            else:
                prev_start = start
                prev_end = end
                prev_word = word
                prev_speaker = speaker
            #Convert to first letter of first word in sentence to uppercase
            if prev_word == ".":
                if prev_speaker != speaker:
                    alternating_sub_colors = rotate(alternating_sub_colors,1)
                word = alternating_sub_colors[0]+first_letter_to_upper(word)
            if index != 0:
                if word == ".":
                    if "\n" in subtitle:
                        subtitle = subtitle.strip()+word
                        formatted_subtitle = format_subtitle(insert_index,subtitle,float(sentence_start_time),float(prev_end))
                        srt_file.write(formatted_subtitle+"\n")
                        insert_index += 1
                        subtitle = ""
                    else:
                        subtitle = subtitle.strip()+word+"\n"
                else:
                    if "\n" in subtitle:
                        first_row,second_row = subtitle.split("\n",1)
                        unformatted_row = get_unformatted_string(second_row)+" "+get_unformatted_string(word)
                        unformatted_row = unformatted_row.strip()
                        if len(unformatted_row) > MAX_COLUMN:
                            formatted_subtitle = format_subtitle(insert_index,subtitle,float(sentence_start_time),float(prev_end))
                            srt_file.write(formatted_subtitle+"\n")
                            sentence_start_time = start.strip()
                            insert_index += 1
                            subtitle = alternating_sub_colors[0]+word+" "
                        else:
                            subtitle += word+" "
                    else:
                        unformatted_row = get_unformatted_string(subtitle)+" "+get_unformatted_string(word)
                        unformatted_row = unformatted_row.strip()
                        if len(unformatted_row) > MAX_COLUMN:
                            subtitle += "\n"+alternating_sub_colors[0]+word+" "
                        else:
                            subtitle += word+" "
            else:
                word = alternating_sub_colors[0]+first_letter_to_upper(word)
                subtitle += word+" "
        index += 1

    if len(subtitle) > 0:
        if "\n" in subtitle:
            first_row,second_row = subtitle.split("\n",1)
            formatted_subtitle = format_subtitle(insert_index,subtitle,float(sentence_start_time),float(prev_end))
            srt_file.write(formatted_subtitle+"\n")
        else:
            formatted_subtitle = format_subtitle(insert_index,subtitle,float(sentence_start_time),float(prev_end))
            srt_file.write(formatted_subtitle+"\n")
    srt_file.close()

def conv_time_value(value):
    conv_value = ""
    if value < 10:
        conv_value = "0"+str(value)
    else:
        conv_value = str(value)
    return conv_value

def conv_second_value(value):
    string_value = str(value)
    string_value = string_value.replace(".",",")
    return string_value


def format_subtitle(insert_index,text,start_time,end_time):
    start_H = int((start_time)/(3600.0))
    start_M = int((float((start_time)/(3600.0))-int((start_time)/(3600.0)))*60)
    start_S = float((float((start_time)/(60.0))-int((start_time)/(60.0)))*60)
    start_S = "%.3f" % start_S
    end_H = int((end_time)/(3600.0))
    end_M = int((float((end_time)/(3600.0))-int((end_time)/(3600.0)))*60)
    end_S = float((float((end_time)/(60.0))-int((end_time)/(60.0)))*60)
    end_S = "%.3f" % end_S
    time_string = conv_time_value(start_H)+":"+conv_time_value(start_M)+":"+conv_second_value(start_S)+" --> "+conv_time_value(end_H)+":"+conv_time_value(end_M)+":"+conv_second_value(end_S)
    formatted_string = str(insert_index)+"\n"+time_string+"\n"+text+"\n"
    return formatted_string

def first_letter_to_upper(first_word):
    if first_word[0] == "å" or first_word[0] == "ä" or first_word[0] == "ö":
        first_word = first_word[0].replace("å","Å")+first_word[1:]
        first_word = first_word[0].replace("ä","Ä")+first_word[1:]
        first_word = first_word[0].replace("ö","Ö")+first_word[1:]
    else:
        first_word = first_word[0].upper()+first_word[1:]
    return first_word

def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))

def target_audio_filename(original_filename):
    file_id = ""
    target_wav_filename = ""
    if "/" in original_filename:
        file_path,file_id = original_filename.rsplit("/",1)
        if "." in file_id:
            file_id,ext = file_id.rsplit(".",1)
        target_wav_filename = file_path+"/"+file_id+"_16k.wav"
    else:
        if "." in original_filename:
            file_id,ext = original_filename.rsplit(".",1)
        else:
            file_id = original_filename
        target_wav_filename = file_id+"_16k.wav"
    return target_wav_filename


def convert_to_target_wav(SOX_PATH,sample_rate,original_filename,original_cwd):
    #Check if input is YouTube link
    if original_filename.startswith("https://") == True or original_filename.startswith("http://") == True:
        #Download link
        YOUTUBE_DL_PATH = SOX_PATH.replace("sox","youtube-dl")
        #original_audio_filename = original_cwd+"/"+id_generator()+".wav"
        #output_video_filename = original_cwd+"/"+id_generator()
        p = subprocess.Popen([YOUTUBE_DL_PATH,"--id","--get-id",original_filename],stdout=PIPE)
        p.wait()
        video_file_id = p.communicate()[0]

        os.system(YOUTUBE_DL_PATH+" --id "+original_filename)

        video_file_id = original_cwd+"/"+video_file_id.strip()+".*"

        output_video_filename = glob.glob(video_file_id)[0]

        #print p.returncode
        #output_video_filename = output_video_filename.strip()+".mp4"
        #print "output_video_filename:"
        #print output_video_filename


        #Convert video to audio
        AVCONV_PATH = SOX_PATH.replace("sox","avconv")
        original_audio_filename = output_video_filename+".wav"
        os.system(AVCONV_PATH+" -i "+output_video_filename+" -vn -f wav -ar 16000 -ac 1 "+original_audio_filename)

        #Convert to target audio
        target_wav_filename = original_audio_filename.replace(".wav","_16k.wav")
        os.system(SOX_PATH+" "+original_audio_filename+" -t wav -r "+str(sample_rate)+" -b 16 -e signed-integer -c 1 "+target_wav_filename)
        os.remove(original_audio_filename)
        os.remove(output_video_filename)
    else:
        #Check if input is video
        if original_filename.endswith(".wav") == False:
            target_wav_filename = target_audio_filename(original_filename)
            AVCONV_PATH = SOX_PATH.replace("sox","avconv")
            original_audio_filename = original_filename+".wav"
            os.system(AVCONV_PATH+" -i "+original_filename+" -vn -f wav -ar 16000 -ac 1 "+original_audio_filename)
            #Convert audio to target wav
            os.system(SOX_PATH+" "+original_audio_filename+" -t wav -r "+str(sample_rate)+" -b 16 -e signed-integer -c 1 "+target_wav_filename)
            os.remove(original_audio_filename)
        else:
            target_wav_filename = target_audio_filename(original_filename)
            #Convert audio to target wav
            os.system(SOX_PATH+" "+original_filename+" -t wav -r "+str(sample_rate)+" -b 16 -e signed-integer -c 1 "+target_wav_filename)
    return target_wav_filename


def get_file_id(filename):
    file_id = ""
    if "/" in filename:
        file_path,file_id = filename.rsplit("/",1)
        if "." in file_id:
            file_id,ext = file_id.rsplit(".",1)
    else:
        if "." in filename:
            file_id,ext = filename.rsplit(".",1)
        else:
            file_id = filename
    return file_id


def write_txt(asr_segmentations,txt_filename):
    txt_file = open(txt_filename,"w")
    snt = ""
    for seg in asr_segmentations:
        start = str(seg[0])
        end = str(seg[1])
        token = str(seg[2])
        speaker_id = str(seg[3])
        if len(snt) == 0:
            token = first_letter_to_upper(token)
            snt += speaker_id+": "+token+" "
        else:
            if token == ".":
                snt = snt.strip()+token.strip()
                txt_file.write(snt+"\n")
                snt = ""
            else:
                snt += token+" "
    if len(snt) > 0:
        snt = snt.strip()+"."
        txt_file.write(snt+"\n")
        snt = ""
    txt_file.close()

def num_of_speakers(segs):
    speakers = []
    for seg in segs:
        speaker = seg['speaker']
        if speaker not in speakers:
            speakers.append(speaker)
    return len(speakers)




asr_models = {
    'fi-params': { 'am-model': 'models/fi/all-lstm',
             'ivec-extractor':'ivec/fi/all-lstm',
             'decoding-graph':'models/fi/all-lstm/graph_kielipankki_word',
             'decode-params':"--extra-left-context 40 --extra-right-context 0 --frames-per-chunk 140 --beam 20 --lattice-beam 10.0 --min-active 12000 --skip-scoring false --num-threads 4 --cmd run.pl --post-decode-acwt 10.0 --acwt 1.0 --scoring-opts \"--min-lmwt 4 --max-lmwt 18 --decode_mbr true\" --online-ivector-dir",
             'lattice-params':'--acoustic-scale=0.071428571',
             'srate': 16000,
             'frame-subsampling-factor' : 3,
             'lang':'Finnish',
             'default': True },

    'fi-morph': { 'am-model': 'models/fi/all-lstm',
             'ivec-extractor':'ivec/fi/all-lstm',
             'decoding-graph':'models/fi/all-lstm/graph_kielipankki_morph1',
             'decode-params':"--extra-left-context 40 --extra-right-context 0 --frames-per-chunk 140 --beam 20 --lattice-beam 10.0 --min-active 12000 --skip-scoring false --num-threads 4 --cmd run.pl --post-decode-acwt 10.0 --acwt 1.0 --scoring-opts \"--min-lmwt 4 --max-lmwt 18 --decode_mbr true\" --online-ivector-dir",
             'lattice-params':'--acoustic-scale=0.071428571',
             'srate': 16000,
             'frame-subsampling-factor' : 3,
             'lang':'Finnish'},

    'fi-conversational': { 'am-model': 'models/fi/all-lstm',
             'ivec-extractor':'ivec/fi/all-lstm',
             'decoding-graph':'models/fi/all-lstm/suomi24',
             'decode-params':"--extra-left-context 40 --extra-right-context 0 --frames-per-chunk 140 --beam 20 --lattice-beam 10.0 --min-active 12000 --skip-scoring false --num-threads 4 --cmd 'slurm.pl --mem 8G' --post-decode-acwt 10.0 --acwt 1.0 --scoring-opts \"--min-lmwt 4 --max-lmwt 18 --decode_mbr true\" --online-ivector-dir",
             'lattice-params':'--acoustic-scale=0.10',
             'srate': 16000,
             'frame-subsampling-factor' : 3,
             'lang':'Finnish'},

    'swe': { 'am-model': 'models/swe/comb_std_tdnn_lstm_9_a',
             'ivec-extractor':'ivec/swe/comb_std_tdnn_lstm_9_a',
             'decoding-graph':'models/swe/comb_std_tdnn_lstm_9_a/graph_word_400k',
             'decode-params':"--extra-left-context 40 --extra-right-context 0 --frames-per-chunk 140 --beam 20 --lattice-beam 10.0 --min-active 12000 --skip-scoring false --num-threads 4 --cmd 'slurm.pl --mem 8G' --post-decode-acwt 10.0 --acwt 1.0 --scoring-opts \"--min-lmwt 4 --max-lmwt 18 --decode_mbr true\" --online-ivector-dir",
             'lattice-params':'--acoustic-scale=0.111111111',
             'srate': 16000,
             'frame-subsampling-factor' : 3,
             'lang':'Swedish'},
}

default_args = {
    'model': [m for m, s in asr_models.items() if 'default' in s][0],
    }


def main():
    #Read command line parameters
    parser = argparse.ArgumentParser(description='Kaldi ASR')

    parser.add_argument('input', help='input, media filename (wav,mp4,avi,webm) or YouTube link',nargs=1)
    parser.add_argument('--eaf',dest='eaf_filename',type=str,help='Outputs recognition result as EAF file',required=False)
    parser.add_argument('--srt',dest='srt_filename',type=str,help='Outputs recognition result as SRT subtitle file',required=False)
    parser.add_argument('--txt',dest='txt_filename',type=str,help='Outputs recognition results as normal text file',required=False)
    parser.add_argument('--diarization',dest='diarization',type=str,help='Diarization algorithm (LIUM)',choices=['LIUM'],required=False)
    parser.add_argument('-M', '--model', help='ASR model to use; "-M list" for list [default "%(default)s"]',default=default_args['model'],
                            metavar='M', choices=['list']+list(asr_models.keys()),action=ModelAction)
    args = vars(parser.parse_args())

    original_cwd = os.getcwd()

    if args['input'][0].startswith("http") == False:
        wav_filename = os.path.realpath(args['input'][0])
    else:
        wav_filename = args['input'][0]
    asr_model_name = args['model']
    asr_model = asr_models[asr_model_name]
    SOX_PATH = "sox"
    AM_MODEL = rootdir+asr_model['am-model']
    IVEC_EXTRACTOR = rootdir+asr_model['ivec-extractor']
    DECODING_GRAPH = rootdir+asr_model['decoding-graph']
    FRAME_SUBSAMPLING_FACTOR = asr_model['frame-subsampling-factor']
    srate = asr_model['srate']
    decode_params = asr_model['decode-params']
    lattice_params = asr_model['lattice-params']


    #Convert media file to target wav
    target_wav_filename = convert_to_target_wav(SOX_PATH,srate,wav_filename,original_cwd)

    os.chdir(rootdir)
    #Segmentation and diarization

    if args['diarization'] == 'None':
        seg_audiofiles = split_audio(SOX_PATH,srate,target_wav_filename)
    elif args['diarization'] == 'LIUM':
        seg_audiofiles = LIUM_speaker_segment_audio(SOX_PATH,srate,target_wav_filename)
    else:
        seg_audiofiles = split_audio(SOX_PATH,srate,target_wav_filename)



    #Prepare data folder
    data_folder_path = create_data_folder(target_wav_filename,seg_audiofiles)
    number_of_speakers = num_of_speakers(seg_audiofiles)

    #JUHO added symbolic link to model due to decode.sh update
    os.system("ln -s "+AM_MODEL+"/final.mdl "+data_folder_path+"/final.mdl")

    #Generate MFCC features and iVectors
    log_path = data_folder_path+"/log"
    mfcc_path = data_folder_path+"/mfccs"
    cmvn_path = data_folder_path+"/cmvn"
    ivectors_dir = data_folder_path+"/ivectors"
    os.system(rootdir+"utils/fix_data_dir.sh "+data_folder_path)
    os.system(rootdir+"steps/make_mfcc.sh --mfcc-config "+rootdir+"conf/mfcc_hires.conf --nj "+str(number_of_speakers)+" "+data_folder_path+" "+log_path+" "+mfcc_path)
    os.system(rootdir+"steps/compute_cmvn_stats.sh "+data_folder_path+" "+log_path+" "+cmvn_path)
    os.system(rootdir+"steps/online/nnet2/extract_ivectors_online.sh --nj "+str(number_of_speakers)+" "+data_folder_path+" "+IVEC_EXTRACTOR+" "+ivectors_dir)

    #Recognize
    decoding_output_path = data_folder_path+"/decode_output"
    os.system(rootdir+"steps/nnet3/decode.sh --nj "+str(number_of_speakers)+" "+decode_params+" "+ivectors_dir+" "+DECODING_GRAPH+" "+data_folder_path+" "+decoding_output_path)

    #Print output
    decode_output_filename = decoding_output_path+"/decode.output.txt"
    lattice_scale = "lattice-scale"
    lattice_add_penalty = "lattice-add-penalty"
    lattice_best_path = "lattice-best-path"
    lattice_1best = "lattice-1best"
    lattice_align_words = "lattice-align-words"
    nbest_to_ctm = "nbest-to-ctm"

    #CTM output
    media_file_id = get_file_id(target_wav_filename)
    ctm_filename = decoding_output_path+"/decode_"+media_file_id+".ctm"
    os.system(lattice_1best+" "+lattice_params+" \"ark:gunzip -c \""+decoding_output_path+"/lat.*.gz\" |\" ark:- | "+lattice_align_words+" "+DECODING_GRAPH+"/phones/word_boundary.int "+AM_MODEL+"/final.mdl ark:- ark:- | "+nbest_to_ctm+"  ark:- "+ctm_filename)

    words_filename = DECODING_GRAPH+"/words.txt"

    words_dict = dict()
    for line in fileinput.input(words_filename):
        line = line.strip()
        token,token_id = line.split(" ",1)
        words_dict[token_id] = token

    trn_dict = dict()
    seg_trns = dict()
    start_refs = dict()
    speaker_refs = dict()
    for seg_audiofile in seg_audiofiles:
        start_seconds = seg_audiofile['start']
        seg_filename = seg_audiofile['file']
        speaker_id = seg_audiofile['speaker']
        seg_id = get_file_id(seg_filename)
        start_refs[seg_id] = float(start_seconds)
        speaker_refs[seg_id] = speaker_id



    asr_word_segmentations = []
    index = 0
    current_seg_id = ""
    sub_word = ""
    sub_word_start = ""
    sub_word_end = ""
    for line in fileinput.input(ctm_filename):
        line = line.strip()
        seg_id,ext,start,dur,word_index = line.split(" ",4)
        start_offset = start_refs[seg_id]
        start_time = float(start)*FRAME_SUBSAMPLING_FACTOR+start_offset
        end_time = float(start)*FRAME_SUBSAMPLING_FACTOR+float(dur)+start_offset
        token = words_dict[word_index]
        speaker = speaker_refs[seg_id]
        #print str(start_time),str(end_time),token,speaker
        if (index != 0) and (current_seg_id != seg_id):
            prev_segmentation = asr_word_segmentations[index-1]
            old_start_time = prev_segmentation[0]
            old_end_time = prev_segmentation[1]
            old_token = prev_segmentation[2]
            old_speaker = prev_segmentation[3]
            asr_word_segmentations.append((old_end_time,start_time,".",old_speaker))
            index += 1
        if token.startswith("+") == False and token.endswith("+") == True:
            sub_word_start = start_time
            sub_word = token.replace("+","")
        elif token.startswith("+") == True and token.endswith("+") == True:
            sub_word += token.replace("+","")
        elif token.startswith("+") == True and token.endswith("+") == False:
            sub_word_end = end_time
            sub_word += token.replace("+","")
            start_time = float(sub_word_start)
            end_time = float(sub_word_end)
            token = str(sub_word)
            asr_word_segmentations.append((start_time,end_time,token,speaker))
            sub_word = ""
            sub_word_start = ""
            sub_word_end = ""
            index += 1
        else:
            asr_word_segmentations.append((start_time,end_time,token,speaker))
            index += 1
        current_seg_id = seg_id


    asr_word_segmentations.sort()

    os.chdir(original_cwd)
    if args['eaf_filename'] != None:
        asr_word_segmentations_ms = alignment_to_milliseconds(asr_word_segmentations)
        eaf_filename = os.path.realpath(args['eaf_filename'])
        write_elan(wav_filename,asr_word_segmentations_ms,eaf_filename)
    if args['srt_filename'] != None:
        srt_filename = os.path.realpath(args['srt_filename'])
        write_srt(asr_word_segmentations,srt_filename)
    if args['txt_filename'] != None:
        txt_filename = os.path.realpath(args['txt_filename'])
        write_txt(asr_word_segmentations,txt_filename)
    #Remove temp recognition files
    remove_temp_files(target_wav_filename,seg_audiofiles,data_folder_path)
    sys.exit()

if __name__ == '__main__':
    main()
