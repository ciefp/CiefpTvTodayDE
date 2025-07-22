# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os
import logging
import requests
import gzip
import xml.etree.ElementTree as ET
from io import BytesIO
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from Plugins.Plugin import PluginDescriptor
from Tools.LoadPixmap import LoadPixmap
import datetime
import time
import subprocess

# Try to import lxml, install if not available
try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False
    logging.getLogger("CiefpTvTodayDE").warning("lxml module not found, attempting to install...")
    try:
        subprocess.run(["pip3", "install", "lxml"], check=True, capture_output=True, text=True)
        from lxml import etree
        LXML_AVAILABLE = True
        logging.getLogger("CiefpTvTodayDE").info("Successfully installed lxml")
    except Exception as e:
        logging.getLogger("CiefpTvTodayDE").error(f"Failed to install lxml: {str(e)}. Falling back to ElementTree")
        LXML_AVAILABLE = False

PLUGIN_PATH = "/usr/lib/enigma2/python/Plugins/Extensions/CiefpTvTodayDE"
EPG_DIR = "/tmp/CiefpTvTodayDE"
PICON_DIR = os.path.join(PLUGIN_PATH, "picon")
PLACEHOLDER_PICON = os.path.join(PLUGIN_PATH, "placeholder.png")
EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz"
CACHE_TIME = 86400  # 24 hours caching

# Configure logging for picons and critical errors only
logging.getLogger('').handlers = []
logging.getLogger("CiefpTvTodayDE").handlers = []
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/ciefp_epgshare.log', mode='a'),
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger("CiefpTvTodayDE")
logger.debug("Initializing CiefpTvTodayDE logger")

def clean_channel_name(name):
    return ''.join(e.lower() if e.isalnum() or e == '.' else '' for e in name).strip()

class CiefpTvTodayDE(Screen):
    skin = """
        <screen name="CiefpTvTodayDE" position="center,center" size="1800,800" title="..:: CiefpTvTodayDE v1.1 ::..">
            <widget name="channelList" position="0,0" size="350,668" scrollbarMode="showAlways" itemHeight="33" font="Regular;28" />
            <widget name="epgInfo" position="370,0" size="1000,668" scrollbarMode="showAlways" itemHeight="33" font="Regular;28" />
            <widget name="sideBackground" position="1380,0" size="420,668" alphatest="on" />
            <widget name="picon" position="0,668" size="220,132" alphatest="on" />
            <widget name="pluginLogo" position="220,668" size="220,132" alphatest="on" />
            <widget name="backgroundLogo" position="440,668" size="1360,132" alphatest="on" />
        </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        self["channelList"] = MenuList([], enableWrapAround=True)
        self["epgInfo"] = MenuList([], enableWrapAround=True)
        self["picon"] = Pixmap()
        self["pluginLogo"] = Pixmap()
        self["backgroundLogo"] = Pixmap()
        self["sideBackground"] = Pixmap()

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"],
            {
                "ok": self.switchView,
                "cancel": self.exit,
                "up": self.up,
                "down": self.down
            }, -1)

        self.currentView = "channels"
        self.epgData = {}
        self.channelData = []
        self.epgLines = []
        self.epgScrollPos = 0
        self.focus_on_channels = True

        for directory in [EPG_DIR, PICON_DIR]:
            if not os.path.exists(directory):
                try:
                    os.makedirs(directory)
                except Exception as e:
                    logger.error(f"Error creating directory {directory}: {str(e)}")
                    self["epgInfo"].setList([f"Error: {str(e)}"])

        self.onLayoutFinish.append(self.loadPluginLogo)
        self.onLayoutFinish.append(self.loadBackgroundLogo)
        self.onLayoutFinish.append(self.loadSideBackground)
        self.onLayoutFinish.append(self.downloadAndParseData)

    def downloadAndParseData(self):
        cache_file = os.path.join(EPG_DIR, "epg_cache.xml")
        
        if os.path.exists(cache_file) and (time.time() - os.path.getmtime(cache_file)) < CACHE_TIME:
            try:
                with open(cache_file, 'r') as f:
                    xml_data = f.read()
                self.parseXMLData(xml_data)
                return
            except Exception as e:
                logger.error(f"Error reading cache: {str(e)}")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "en-US,en;q=0.5"
            }
            response = requests.get(EPG_URL, headers=headers, timeout=30)
            response.raise_for_status()

            with gzip.GzipFile(fileobj=BytesIO(response.content)) as gz:
                xml_data = gz.read().decode('utf-8')

            try:
                with open(cache_file, 'w') as f:
                    f.write(xml_data)
            except Exception as e:
                logger.error(f"Error saving cache: {str(e)}")

            self.parseXMLData(xml_data)

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error: {str(e)}")
            self["epgInfo"].setList([f"Network error: {str(e)}"])
        except Exception as e:
            logger.error(f"General error: {str(e)}")
            self["epgInfo"].setList([f"Error: {str(e)}"])

    def parseXMLData(self, xml_data):
        try:
            if LXML_AVAILABLE:
                parser = etree.XMLParser(encoding='utf-8', recover=True)
                tree = etree.fromstring(xml_data.encode('utf-8'), parser=parser)
                channel_iter = tree.xpath('//channel')
                program_iter = tree.xpath('//programme')
            else:
                tree = ET.fromstring(xml_data)
                channel_iter = tree.findall('.//channel')
                program_iter = tree.findall('.//programme')

            self.channelData = []
            self.epgData = {}

            for channel in channel_iter:
                channel_id = channel.get('id')
                display_name = channel.find('display-name') if not LXML_AVAILABLE else channel.xpath('display-name[1]/text()')
                icon = channel.find('icon') if not LXML_AVAILABLE else channel.xpath('icon[1]/@src')

                if not channel_id or display_name is None:
                    continue

                channel_name = display_name.text.strip() if not LXML_AVAILABLE else display_name[0].strip()
                self.channelData.append({
                    "id": channel_id,
                    "title": channel_name,
                    "alias": clean_channel_name(channel_name),
                    "logo": f"{clean_channel_name(channel_name)}.png",
                    "icon": icon.get('src') if icon is not None and not LXML_AVAILABLE else (icon[0] if icon else None)
                })
                self.epgData[channel_name] = []

            if not self.channelData:
                self["epgInfo"].setList(["No channels found in EPG data"])
                self["channelList"].setList(["No channels available"])
                return

            self["channelList"].setList([ch["title"] for ch in self.channelData])

            for program in program_iter:
                channel_id = program.get('channel')
                start_time = program.get('start')
                stop_time = program.get('stop')
                title = program.find('title') if not LXML_AVAILABLE else program.xpath('title[1]/text()')
                desc = program.find('desc') if not LXML_AVAILABLE else program.xpath('desc[1]/text()')
                category = program.find('category') if not LXML_AVAILABLE else program.xpath('category[1]/text()')
                icon = program.find('icon') if not LXML_AVAILABLE else program.xpath('icon[1]/@src')

                if not (channel_id and start_time and (title is not None if not LXML_AVAILABLE else title)):
                    continue

                channel = next((ch for ch in self.channelData if ch['id'] == channel_id), None)
                if not channel:
                    continue

                channel_name = channel['title']
                program_data = {
                    'title': title.text.strip() if not LXML_AVAILABLE else (title[0].strip() if title else "Nepoznat naslov"),
                    'desc': desc.text.strip() if desc is not None and not LXML_AVAILABLE else (desc[0].strip() if desc else "Nema opisa"),
                    'category': category.text.strip() if category is not None and not LXML_AVAILABLE else (category[0].strip() if category else ""),
                    'icon': icon.get('src') if icon is not None and not LXML_AVAILABLE else (icon[0] if icon else None)
                }

                try:
                    time_str = start_time.split(' ')[0]
                    time_obj = datetime.datetime.strptime(time_str, '%Y%m%d%H%M%S')
                    start_timestamp = int(time_obj.timestamp())
                    if stop_time:
                        stop_time_str = stop_time.split(' ')[0]
                        stop_time_obj = datetime.datetime.strptime(stop_time_str, '%Y%m%d%H%M%S')
                        program_data['stop_timestamp'] = int(stop_time_obj.timestamp())
                    else:
                        program_data['stop_timestamp'] = None
                    program_data['start_timestamp'] = start_timestamp
                    program_data['start_date'] = time_obj.strftime('%Y%m%d')
                    self.epgData[channel_name].append(program_data)
                except ValueError as e:
                    logger.error(f"Time parsing error for program {title.text if not LXML_AVAILABLE else (title[0] if title else 'unknown')}: {str(e)}")
                    continue

            self.updateEPGAndPicon()

        except Exception as e:
            logger.error(f"XML parsing error: {str(e)}")
            self["epgInfo"].setList([f"Error parsing EPG data: {str(e)}"])
            self["channelList"].setList(["Error loading channels"])

    def getEPGFromData(self, channel_name):
        epglist = self.epgData.get(channel_name, [])
        if not epglist:
            return [f"No EPG data for channel: {channel_name}"]

        epg_by_date = {}
        for program in sorted(epglist, key=lambda x: x['start_timestamp']):
            try:
                date_str = program['start_date']
                date_formatted = datetime.datetime.fromtimestamp(program['start_timestamp']).strftime('%d.%m.%Y')
                time_str = datetime.datetime.fromtimestamp(program['start_timestamp']).strftime('%H:%M')
                entry = f"{time_str} - {program['title']} ({program['category']})"
                if program['desc']:
                    entry += f"\n  {program['desc']}"
                if date_str not in epg_by_date:
                    epg_by_date[date_str] = []
                epg_by_date[date_str].append(entry)
            except ValueError as e:
                logger.error(f"Time formatting error for program {program['title']}: {str(e)}")
                continue

        result = []
        for date_str in sorted(epg_by_date.keys()):
            date_formatted = datetime.datetime.strptime(date_str, '%Y%m%d').strftime('%d.%m.%Y')
            result.append(f"--- {date_formatted} ---")
            result.extend(epg_by_date[date_str])
        
        if not result:
            return [f"No valid EPG data for channel: {channel_name}"]
        return result

    def loadPicon(self, channel_name):
        channel = next((ch for ch in self.channelData if ch["title"] == channel_name), None)
        if not channel:
            logger.error(f"No channel data found for channel: {channel_name}")
            return

        possible_picon_names = [
            channel["logo"],
            channel["alias"] + ".png",
            channel["title"].replace(" ", "_") + ".png",
            channel["title"].replace(" ", "").lower() + ".png"
        ]

        pixmap = None
        found_picon = False

        for picon_name in possible_picon_names:
            filename = os.path.join(PICON_DIR, picon_name)
            if os.path.exists(filename):
                try:
                    pixmap = LoadPixmap(filename)
                    found_picon = True
                    break
                except Exception as e:
                    logger.error(f"Error loading picon {filename}: {str(e)}")
            else:
                logger.error(f"Picon not found: {filename}")

        if not found_picon:
            logger.error(f"No picon found for channel '{channel_name}'. Tried: {', '.join(possible_picon_names)}. Using placeholder.")
            if os.path.exists(PLACEHOLDER_PICON):
                try:
                    pixmap = LoadPixmap(PLACEHOLDER_PICON)
                except Exception as e:
                    logger.error(f"Error loading placeholder picon {PLACEHOLDER_PICON}: {str(e)}")
            else:
                logger.error(f"Placeholder picon not found: {PLACEHOLDER_PICON}")

        if pixmap and self["picon"].instance:
            try:
                self["picon"].instance.setPixmap(pixmap)
            except Exception as e:
                logger.error(f"Error setting picon for channel {channel_name}: {str(e)}")

    def updateEPGAndPicon(self):
        current = self["channelList"].getCurrent()
        if current:
            channel_name = current
            self.epgLines = self.getEPGFromData(channel_name)
            if self.epgLines:
                self["epgInfo"].setList(self.epgLines)
                
                # Find current program
                now = time.time()  # Current time in seconds since epoch
                current_date = datetime.datetime.now().strftime('%Y%m%d')
                current_index = 0
                found_current = False

                # Iterate through epgData to find the current program
                current_program = None
                min_time_diff = float('inf')
                for program in sorted(self.epgData.get(channel_name, []), key=lambda x: x['start_timestamp']):
                    start_time = program['start_timestamp']
                    stop_time = program['stop_timestamp']
                    program_date = program['start_date']
                    if stop_time and program_date == current_date and start_time <= now <= stop_time:
                        # Current program is active
                        time_diff = abs(now - start_time)
                        if time_diff < min_time_diff:
                            min_time_diff = time_diff
                            current_program = program

                if current_program:
                    # Find the index in epgLines that matches the current program
                    for i, line in enumerate(self.epgLines):
                        if line.startswith("---") or "No EPG data" in line or "No valid EPG data" in line:
                            continue
                        if " - " not in line:
                            continue
                        try:
                            time_str = line.split(" - ")[0]
                            title = line.split(" - ")[1].split(" (")[0]
                            if title == current_program['title']:
                                line_time = datetime.datetime.strptime(
                                    f"{datetime.datetime.fromtimestamp(current_program['start_timestamp']).strftime('%d.%m.%Y')} {time_str}",
                                    "%d.%m.%Y %H:%M"
                                ).timestamp()
                                if abs(line_time - current_program['start_timestamp']) < 60:  # Allow 1-minute tolerance
                                    current_index = i
                                    found_current = True
                                    break
                        except (IndexError, ValueError):
                            continue

                if not found_current:
                    # Find the first program for the current or future date
                    future_programs = [p for p in self.epgData.get(channel_name, []) if p['start_date'] >= current_date]
                    if future_programs:
                        first_future = min(future_programs, key=lambda x: x['start_timestamp'])
                        for i, line in enumerate(self.epgLines):
                            if line.startswith("---") or "No EPG data" in line or "No valid EPG data" in line:
                                continue
                            if " - " not in line:
                                continue
                            try:
                                time_str = line.split(" - ")[0]
                                title = line.split(" - ")[1].split(" (")[0]
                                if title == first_future['title']:
                                    line_time = datetime.datetime.strptime(
                                        f"{datetime.datetime.fromtimestamp(first_future['start_timestamp']).strftime('%d.%m.%Y')} {time_str}",
                                        "%d.%m.%Y %H:%M"
                                    ).timestamp()
                                    if abs(line_time - first_future['start_timestamp']) < 60:
                                        current_index = i
                                        break
                            except (IndexError, ValueError):
                                continue
                    else:
                        # If no future programs, select the last valid program
                        for i, line in reversed(list(enumerate(self.epgLines))):
                            if line.startswith("---") or "No EPG data" in line or "No valid EPG data" in line:
                                continue
                            if " - " not in line:
                                continue
                            current_index = i
                            break
                
                self.epgScrollPos = current_index
                self["epgInfo"].moveToIndex(self.epgScrollPos)
                if not self.focus_on_channels:
                    self["epgInfo"].instance.setSelectionEnable(True)
            
            self.loadPicon(channel_name)
        else:
            self["epgInfo"].setList(["Select a channel to view EPG"])

    def switchView(self):
        self.currentView = "epg" if self.currentView == "channels" else "channels"
        self.focus_on_channels = self.currentView == "channels"
        self.epgScrollPos = 0
        self["channelList"].instance.setSelectionEnable(self.focus_on_channels)
        self["epgInfo"].instance.setSelectionEnable(not self.focus_on_channels)
        self.updateEPGAndPicon()

    def exit(self):
        self.close()

    def up(self):
        if self.currentView == "channels":
            self["channelList"].up()
            self.updateEPGAndPicon()
        else:
            self["epgInfo"].up()

    def down(self):
        if self.currentView == "channels":
            self["channelList"].down()
            self.updateEPGAndPicon()
        else:
            self["epgInfo"].down()

    def loadPluginLogo(self):
        logo_path = os.path.join(PLUGIN_PATH, "plugin_logo.png")
        if os.path.exists(logo_path):
            try:
                pixmap = LoadPixmap(logo_path)
                if pixmap and self["pluginLogo"].instance:
                    self["pluginLogo"].instance.setPixmap(pixmap)
            except Exception as e:
                logger.error(f"Error loading plugin logo: {str(e)}")
        else:
            logger.error(f"Plugin logo not found: {logo_path}")

    def loadBackgroundLogo(self):
        logo_path = os.path.join(PLUGIN_PATH, "background_logo.png")
        if os.path.exists(logo_path):
            try:
                pixmap = LoadPixmap(logo_path)
                if pixmap and self["backgroundLogo"].instance:
                    self["backgroundLogo"].instance.setPixmap(pixmap)
            except Exception as e:
                logger.error(f"Error loading background logo: {str(e)}")
        else:
            logger.error(f"Background logo not found: {logo_path}")

    def loadSideBackground(self):
        bg_path = os.path.join(PLUGIN_PATH, "side_background.png")
        if os.path.exists(bg_path):
            try:
                pixmap = LoadPixmap(bg_path)
                if pixmap and self["sideBackground"].instance:
                    self["sideBackground"].instance.setPixmap(pixmap)
            except Exception as e:
                logger.error(f"Error loading side background: {str(e)}")
        else:
            logger.error(f"Side background not found: {bg_path}")

def main(session, **kwargs):
    session.open(CiefpTvTodayDE)

def Plugins(**kwargs):
    return [PluginDescriptor(
        name="CiefpTvTodayDE",
        description="TV Today DE EPG plugin,epgshare v1.1",
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon="icon.png",
        fnc=main
    )]