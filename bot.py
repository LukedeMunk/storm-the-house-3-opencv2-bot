################################################################################
#
# File:     bot.py
# Author:   Luke de Munk
# Brief:    Bot for the Storm the House 3 2D web game. Can be used to
#           automatically shoot enemies with any weapon. For some weapons,
#           accuracy is the limiting factor, and the shooting delay
#           (SHOOTING_DELAY_S) must be increased accordingly.
#           
#           Press '1' to start shooting. Once shooting has started, stopping it
#           can be difficult because the game window may no longer have focus.
#           Shooting stops automatically when a day has passed or when the pause
#           menu is activated ('p' in-game).
#           
#           For certain weapons, the left mouse button must be held down
#           continuously. This can be achieved by pressing '2'.
# 
#           More information: 
#           https://github.com/LukedeMunk/storm-the-house-3-opencv2-bot
#
################################################################################
import mss                                                                      #For capturing the screen
import numpy as np                                                              #For extended data functionality
import cv2                                                                      #For computer vision functionality
import pyautogui                                                                #For showing the game real-time with target overlays
import time                                                                     #For timing functionality
import threading                                                                #Threading for seperate game view loop and shoot loop
from threading import Lock                                                      #For locking shared thread variables
import random                                                                   #For random selection out of target pool
import os                                                                       #For file paths

# -----------------------
# Configure this
# -----------------------
SHOOT_DELAY_S = 0.2

MONITOR = 2
GAME_WINDOW_X = 10
GAME_WINDOW_Y = 130

#region Global constants
TARGET_POOL_SIZE = 4                                                            #A target pool is used to have less shoots on false positives
PATH = os.path.dirname(os.path.realpath(__file__))                              #Current path
TEMPLATE_FOLDER = "template_images"                                             #Folder containing the templates
TEMPLATE_PATH = os.path.join(PATH, TEMPLATE_FOLDER)                             #Full path to that folder

#Template filenames
SOLDIER_TEMPLATE_FILE = "soldier_template.png"
GUNNER_TEMPLATE_FILE = "gunner_template.png"
JEEP_TEMPLATE_FILE = "jeep_template.png"
FLYING_SOLDIER_TEMPLATE_FILE = "flyer_template.png"
FLAME_THROWER_SOLDIER_TEMPLATE_FILE = "flame_thrower_template.png"
APACHE_TEMPLATE_FILE = "apache_template.png"
TANK_TEMPLATE_FILE = "tank_template.png"
ROBOT_TEMPLATE_FILE = "robot_template.png"

#Hotkeys
HOTKEY_ENABLE_SHOOTING = "1"
HOTKEY_ENABLE_HOLD = "2"

#Enemies
ENEMY_TYPE_SOLDIER = 0
ENEMY_TYPE_GUNNER = 1
ENEMY_TYPE_JEEP = 2
ENEMY_TYPE_FLYING_SOLDIER = 3
ENEMY_TYPE_FLAME_THROWER_SOLDIER = 4
ENEMY_TYPE_APACHE = 5
ENEMY_TYPE_TANK = 6
ENEMY_TYPE_ROBOT = 7

TYPE_PRIORITY = {
    ENEMY_TYPE_ROBOT: 0,
    ENEMY_TYPE_TANK: 1,
    ENEMY_TYPE_APACHE: 2,
    ENEMY_TYPE_FLYING_SOLDIER: 3,
    ENEMY_TYPE_FLAME_THROWER_SOLDIER: 4,
    ENEMY_TYPE_JEEP: 5,
    ENEMY_TYPE_GUNNER: 6,
}

ENEMY_INFO = [
    {
        "color": (0, 200, 0),            # Dark green – basic soldier
        "template": SOLDIER_TEMPLATE_FILE,
        "width": 10,
        "height": 28,
        "x_offset": 0,
        "y_offset": 0,
    },
    {
        "color": (0, 255, 255),          # Yellow – gunner (higher threat infantry)
        "template": GUNNER_TEMPLATE_FILE,
        "width": 12,
        "height": 28,
        "x_offset": 2,
        "y_offset": 0,
    },
    {
        "color": (0, 165, 255),          # Orange – jeep / light vehicle
        "template": JEEP_TEMPLATE_FILE,
        "width": 77,
        "height": 35,
        "x_offset": 52,
        "y_offset": 12,
    },
    {
        "color": (255, 255, 0),          # Cyan – flying soldier
        "template": FLYING_SOLDIER_TEMPLATE_FILE,
        "width": 14,
        "height": 38,
        "x_offset": 0,
        "y_offset": 0,
    },
    {
        "color": (0, 140, 255),          # Dark orange – flamethrower (area threat)
        "template": FLAME_THROWER_SOLDIER_TEMPLATE_FILE,
        "width": 22,
        "height": 42,
        "x_offset": 10,
        "y_offset": 0,
    },
    {
        "color": (255, 0, 0),            # Blue-ish red
        "template": APACHE_TEMPLATE_FILE,
        "width": 120,
        "height": 30,
        "x_offset": 80,
        "y_offset": 15,
    },
    {
        "color": (0, 0, 255),            # Red – tank (highest vehicle threat)
        "template": TANK_TEMPLATE_FILE,
        "width": 145,
        "height": 55,
        "x_offset": 75,
        "y_offset": 2,
    },
    {
        "color": (255, 0, 255),          # Magenta – robot / boss-type enemy
        "template": ROBOT_TEMPLATE_FILE,
        "width": 85,
        "height": 130,
        "x_offset": 20,
        "y_offset": 8,
    }
]
#endregion

#region Global variables
menu_frames = 0                                                                 #A 'debounce' for stopping shooting. Explosions can cause menu detection false positives
enemies = []
latest_enemy = None
enemy_lock = Lock()

is_shooting = False
is_reloading = False

last_shot_time = 0.0
shots_fired = 0

#Toggles
shooting_enabled = False
hold_mouse_button = False

#Locations
game_window = None
enemy_region = None
critical_enemy_region = None
#endregion

# Initialize game window and enemy regions
with mss.mss() as sct:
    game_window = sct.monitors[MONITOR].copy()
    game_window["top"] += GAME_WINDOW_Y
    game_window["left"] += GAME_WINDOW_X
    game_window["width"] = 650
    game_window["height"] = 520

    enemy_region = game_window.copy()
    enemy_region["top"] += 50
    enemy_region["width"] = 500
    enemy_region["height"] = 350

    critical_enemy_region = game_window.copy()
    critical_enemy_region["top"] += 200
    critical_enemy_region["left"] += 350
    critical_enemy_region["width"] = 150
    critical_enemy_region["height"] = 200
    
################################################################################
#
#   @brief  Scans the specified frames for enemies, puts the enemies in the
#           global enemy list and sorts the enemies based on priority and
#           location.
#   @param  enemy_region_frame              Frame (BGR)
#   @param  critical_enemy_region_frame     Critical enemy region frame (BGR)
#
################################################################################
def get_enemy_coordinates(enemy_region_frame, critical_enemy_region_frame):
    global enemies

    enemies = []
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_SOLDIER, 0.85))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_GUNNER, 0.85))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_JEEP))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_FLYING_SOLDIER))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_FLAME_THROWER_SOLDIER, 0.75))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_APACHE, 0.96))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_TANK, 0.86))
    enemies.extend(get_enemy_coordinates_by_template(enemy_region_frame, ENEMY_TYPE_ROBOT, 0.98))
    enemies.extend(get_soldier_coordinates_by_color(critical_enemy_region_frame))

    #Closest to the castle
    cx = enemy_region["width"] 
    cy = enemy_region["height"] / 2
        
    enemies.sort(
        key=lambda e: (
            TYPE_PRIORITY.get(e["type"], 99),
            abs((e["x"] + e["w"] / 2) - cx) +
            abs((e["y"] + e["h"] / 2) - cy)
        )
    )

################################################################################
#
#   @brief  Shoots on the specified coordinates.
#   @param  coordinates         Dictionary containing the coordinates
#
################################################################################
def shoot(coordinates):
    global shots_fired, is_shooting
    if not is_reloading:
        shots_fired += 1

    screen_x = game_window["left"] + coordinates["x"] + coordinates["w"] / 2
    screen_y = game_window["top"] + coordinates["y"] + coordinates["h"] / 2

    if not hold_mouse_button:
        pyautogui.click(screen_x, screen_y)
        return

    if not is_shooting:
        pyautogui.mouseDown(screen_x, screen_y)
        is_shooting = True
    else:
        pyautogui.moveTo(screen_x, screen_y)
    
################################################################################
#
#   @brief  Returns a random enemy from the target pool.
#   @return                     Dict with enemy coordinates
#
################################################################################
def get_random_latest_enemy():
    if not enemies:
        return None
    
    pool = enemies[:TARGET_POOL_SIZE]

    return random.choice(pool)

################################################################################
#
#   @brief  Returns a list of enemy coordinates detected via template matching.
#   @param  enemy_region_frame  Frame (BGR)
#   @param  type                Enemy type to look for
#   @param  threshold           Matching threshold
#   @return list                List of enemies
#
################################################################################
def get_enemy_coordinates_by_template(enemy_region_frame, type, threshold = 0.8):
    template = cv2.imread(os.path.join(TEMPLATE_PATH, ENEMY_INFO[type]["template"]))
    if template is None:
        raise FileNotFoundError("Not all templates found in working directory")
    
    enemies = []

    #Restrict search to enemy_region
    rx = enemy_region["left"] - game_window["left"]
    ry = enemy_region["top"] - game_window["top"]

    #Convert to gray for speed
    gray_roi = cv2.cvtColor(enemy_region_frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    w, h = gray_template.shape[::-1]

    res = cv2.matchTemplate(gray_roi, gray_template, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    
    seen = set()                                                                #To avoid duplicate nearby detections
    for pt in zip(*loc[::-1]):                                                  #Switch x/y order
        #Simple non-max suppression by distance
        if any(abs(pt[0]-sx) < w//2 and abs(pt[1]-sy) < h//2 for sx, sy in seen):
            continue

        seen.add(pt)

        enemies.append({
            "type": type,
            "x": pt[0] + rx - ENEMY_INFO[type]["x_offset"],
            "y": pt[1] + ry - ENEMY_INFO[type]["y_offset"],
            "w": ENEMY_INFO[type]["width"],
            "h": ENEMY_INFO[type]["height"]
        })

    return enemies

################################################################################
#
#   @brief  Returns soldiers based on color. To improve the soldier template
#           matching results in the critical enemy region.
#   @param  enemy_region_frame  Frame (BGR)
#   @return list                List of soldiers
#
################################################################################
def get_soldier_coordinates_by_color(enemy_region_frame):
    soldiers = []

    gray = cv2.cvtColor(enemy_region_frame, cv2.COLOR_BGR2GRAY)
    mask = cv2.inRange(gray, 0, 8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    ry = critical_enemy_region["top"] - game_window["top"]
    rx = critical_enemy_region["left"] - game_window["left"]

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        #False positives, too small
        if w < 10 and h < 10:
            continue

        #Deaths
        if w > 10 and h < 20:
            continue

        y += ry
        x += rx

        enemy = {
            "type": ENEMY_TYPE_SOLDIER,
            "x": x,
            "y": y,
            "w": w,
            "h": h
        }

        soldiers.append(enemy)

    return soldiers
    
################################################################################
#
#   @brief  Checks the ammunition bar to check whether is reloading or not.
#   @param  frame               Frame to check
#
################################################################################
def check_reloading(frame):
    global is_reloading

    start_px = frame[10, 8]                                                     #y, x
    end_px = frame[10, 208]                                                     #y, x

    #Check if is empty
    if not is_reloading and not np.allclose(start_px, [38, 34, 46], atol=20):
        is_reloading = True
        return

    #Check if is reloaded
    if is_reloading and np.allclose(end_px, [38, 34, 46], atol=20):
        is_reloading = False

################################################################################
#
#   @brief  Renders the heads-up display. An overview with real-time
#           information.
#   @param  frame               Frame to draw on
#
################################################################################
def render_hud(frame):
    font = cv2.FONT_HERSHEY_PLAIN
    scale = 0.8
    thickness = 1
    
    soldiers = []
    gunners = []
    jeeps = []
    flying_soldiers = []
    flame_throwers = []
    tanks = []
    apaches = []
    robots = []

    if len(enemies) > 0:
        soldiers = [item for item in enemies if item["type"] == ENEMY_TYPE_SOLDIER]
        gunners = [item for item in enemies if item["type"] == ENEMY_TYPE_GUNNER]
        jeeps = [item for item in enemies if item["type"] == ENEMY_TYPE_JEEP]
        flying_soldiers = [item for item in enemies if item["type"] == ENEMY_TYPE_FLYING_SOLDIER]
        flame_throwers = [item for item in enemies if item["type"] == ENEMY_TYPE_FLAME_THROWER_SOLDIER]
        tanks = [item for item in enemies if item["type"] == ENEMY_TYPE_TANK]
        apaches = [item for item in enemies if item["type"] == ENEMY_TYPE_APACHE]
        robots = [item for item in enemies if item["type"] == ENEMY_TYPE_ROBOT]

    status = "ON" if shooting_enabled else "OFF"
    mouse_status = "ON" if hold_mouse_button else "OFF"
    
    cv2.rectangle(
        frame,
        (0, game_window["height"]-120),
        (game_window["left"] + game_window["width"], game_window["height"] + 120),
        (200, 200, 200),
        -1
    )

    cv2.putText(frame, f"Soldiers: {len(soldiers)}",
                (10, 420), font, scale, ENEMY_INFO[ENEMY_TYPE_SOLDIER]["color"], thickness)

    cv2.putText(frame, f"Gunners: {len(gunners)}",
                (10, 432), font, scale, ENEMY_INFO[ENEMY_TYPE_GUNNER]["color"], thickness)

    cv2.putText(frame, f"Jeeps: {len(jeeps)}",
                (10, 444), font, scale, ENEMY_INFO[ENEMY_TYPE_JEEP]["color"], thickness)

    cv2.putText(frame, f"Flying enemies: {len(flying_soldiers)}",
                (10, 456), font, scale, ENEMY_INFO[ENEMY_TYPE_FLYING_SOLDIER]["color"], thickness)

    cv2.putText(frame, f"Flame throwers: {len(flame_throwers)}",
                (10, 468), font, scale, ENEMY_INFO[ENEMY_TYPE_FLAME_THROWER_SOLDIER]["color"], thickness)

    cv2.putText(frame, f"Tanks: {len(tanks)}",
                (10, 480), font, scale, ENEMY_INFO[ENEMY_TYPE_TANK]["color"], thickness)

    cv2.putText(frame, f"Apaches: {len(apaches)}",
                (10, 492), font, scale, ENEMY_INFO[ENEMY_TYPE_APACHE]["color"], thickness)

    cv2.putText(frame, f"Robots: {len(robots)}",
                (10, 504), font, scale, ENEMY_INFO[ENEMY_TYPE_ROBOT]["color"], thickness)
    
    cv2.putText(frame, f"Shots fired: {shots_fired}",
                (150, 420), font, scale, (0, 0, 0), thickness)

    cv2.putText(frame, f"Shooting (1): {status}",
                (150, 432), font, scale, (0, 0, 0), thickness)

    cv2.putText(frame, f"Hold mouse button (2): {mouse_status}",
                (150, 444), font, scale, (0, 0, 0), thickness)

    if is_reloading:
        cv2.putText(frame, f"Reloading",
                    (310, 420), font, scale, (0, 0, 100), thickness)

################################################################################
#
#   @brief  Renders boxes around the enemy regions.
#   @param  frame               Frame to draw on
#
################################################################################
def render_enemy_regions(frame):
    enemy_region_x = enemy_region["left"] - game_window["left"]
    enemy_region_y = enemy_region["top"] - game_window["top"]

    critical_enemy_region_x = critical_enemy_region["left"] - game_window["left"]
    critical_enemy_region_y = critical_enemy_region["top"] - game_window["top"]

    #Draw enemy region
    cv2.rectangle(
        frame,
        (enemy_region_x, enemy_region_y),
        (enemy_region_x + enemy_region["width"]+1, enemy_region_y + enemy_region["height"]),
        (0, 255, 0),
        1
    )

    #Draw critical enemy region
    cv2.rectangle(
        frame,
        (critical_enemy_region_x, critical_enemy_region_y),
        (critical_enemy_region_x + critical_enemy_region["width"], critical_enemy_region_y + critical_enemy_region["height"]),
        (0, 255, 255),
        1
    )

################################################################################
#
#   @brief  Renders boxes around the detected enemies.
#   @param  frame               Frame to draw on
#
################################################################################
def render_enemies(frame):
    for e in enemies:
        cv2.rectangle(
            frame,
            (e["x"], e["y"]),
            (e["x"] + e["w"], e["y"] + e["h"]),
            ENEMY_INFO[e["type"]]["color"],
            1
        )

################################################################################
#
#   @brief  Checks whether a key is pressed and takes the nessecary actions. The
#           pyautogui window needs to be in focus to get the key detected.
#   @return bool                True if the game loop needs to be stopped
#
################################################################################
def check_keys():
    global shooting_enabled, is_shooting, hold_mouse_button

    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        return False
    
    if key == ord(HOTKEY_ENABLE_SHOOTING):
        shooting_enabled = not shooting_enabled
        is_shooting = False
        pyautogui.mouseUp()
    
    if key == ord(HOTKEY_ENABLE_HOLD):
        hold_mouse_button = not hold_mouse_button

    return True

################################################################################
#
#   @brief  Checks whether the shooting needs to be disabled due to an open
#           menu.
#   @param  frame               Frame to check
#
################################################################################
def check_menus(frame):
    global menu_frames

    if not shooting_enabled:
        return
    
    px = frame[game_window["height"] - enemy_region["height"], 5]               #y, x
    if np.allclose(px, [175, 178, 182], atol=0):
        disable_shooting()
        print(px)

    #Pause menu
    if np.allclose(px, [120, 126, 132], atol=0):
        menu_frames += 1
        if menu_frames > 2:
            menu_frames = 0
            disable_shooting()
            print(px)

        return
    
    #Death menu
    if np.allclose(px, [58, 64, 108], atol=0):
        menu_frames += 1
        if menu_frames > 2:
            menu_frames = 0
            disable_shooting()
            print(px)
            
        return

    #Shop menu
    if np.allclose(px, [101, 102, 104], atol=0):
        menu_frames += 1
        if menu_frames > 2:
            menu_frames = 0
            disable_shooting()
            print(px)
            
        return
    
    menu_frames = 0

################################################################################
#
#   @brief  Disables the auto-shooting.
#
################################################################################
def disable_shooting():
    global shooting_enabled, is_shooting

    shooting_enabled = False
    is_shooting = False
    pyautogui.mouseUp()

################################################################################
#
#   @brief  Thread. Render loop for the debug window containing the bot
#           detections and bot HUD.
#
################################################################################
def render_loop():
    global latest_enemy

    with mss.mss() as sct:
        while True:
            grab = sct.grab(game_window)
            frame = cv2.cvtColor(np.array(grab), cv2.COLOR_BGRA2BGR)

            critical_enemy_region_x = critical_enemy_region["left"] - game_window["left"]
            critical_enemy_region_y = critical_enemy_region["top"] - game_window["top"]

            enemy_region_x = enemy_region["left"] - game_window["left"]
            enemy_region_y = enemy_region["top"] - game_window["top"]

            enemy_region_frame = frame[
                                        enemy_region_y:enemy_region_y + enemy_region["height"],
                                        enemy_region_x:enemy_region_x + enemy_region["width"]
                                    ]
            critical_enemy_region_frame = frame[
                                                critical_enemy_region_y:critical_enemy_region_y + critical_enemy_region["height"],
                                                critical_enemy_region_x:critical_enemy_region_x + critical_enemy_region["width"]
                                            ]

            get_enemy_coordinates(enemy_region_frame, critical_enemy_region_frame)
            check_reloading(frame)

            with enemy_lock:
                latest_enemy = get_random_latest_enemy()

            render_enemy_regions(frame)
            render_enemies(frame)
            render_hud(frame)

            cv2.imshow("Game Window", frame)

            check_menus(enemy_region_frame)

            if not check_keys():
                break

        cv2.destroyAllWindows()

################################################################################
#
#   @brief  Thread. Shoot loop that shoots when it is time.
#
################################################################################
def shoot_loop():
    global last_shot_time

    while True:
        if not shooting_enabled:
            time.sleep(0.005)
            continue

        with enemy_lock:
            target = latest_enemy

        if target is None:
            time.sleep(0.005)
            continue

        if SHOOT_DELAY_S == -1:
            for enemy in enemies:
                shoot(enemy)

            time.sleep(0.001)
            continue

        now = time.perf_counter()
        if now - last_shot_time >= SHOOT_DELAY_S:
            shoot(target)
            last_shot_time = now

        time.sleep(0.001)

#Start the threads
render_thread = threading.Thread(target=render_loop, daemon=True)
shoot_thread = threading.Thread(target=shoot_loop, daemon=True)

render_thread.start()
shoot_thread.start()

render_thread.join()