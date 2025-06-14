#!/usr/bin/env python3

# Copyright (c) FIRST and other WPILib contributors.
# Open Source Software; you can modify and/or share it under the terms of
# the WPILib BSD license file in the root directory of this project.

import json
import time
import sys
import cv2
from dt_apriltags import Detector
import numpy as np
import math

from cscore import CameraServer, VideoSource, UsbCamera, MjpegServer
from ntcore import NetworkTableInstance, EventFlags

#   JSON format:
#   {
#       "team": <team number>,
#       "ntmode": <"client" or "server", "client" if unspecified>
#       "cameras": [
#           {
#               "name": <camera name>
#               "path": <path, e.g. "/dev/video0">
#               "pixel format": <"MJPEG", "YUYV", etc>   // optional
#               "width": <video mode width>              // optional
#               "height": <video mode height>            // optional
#               "fps": <video mode fps>                  // optional
#               "brightness": <percentage brightness>    // optional
#               "white balance": <"auto", "hold", value> // optional
#               "exposure": <"auto", "hold", value>      // optional
#               "properties": [                          // optional
#                   {
#                       "name": <property name>
#                       "value": <property value>
#                   }
#               ],
#               "stream": {                              // optional
#                   "properties": [
#                       {
#                           "name": <stream property name>
#                           "value": <stream property value>
#                       }
#                   ]
#               }
#           }
#       ]
#       "switched cameras": [
#           {
#               "name": <virtual camera name>
#               "key": <network table key used for selection>
#               // if NT value is a string, it's treated as a name
#               // if NT value is a double, it's treated as an integer index
#           }
#       ]
#   }



configFile = "/boot/frc.json"

class CameraConfig: pass

team = 2052
server = False
cameraConfigs = []
cameras = []

# Server config and stuff

def parseError(str):
    """Report parse error."""
    print("config error in '" + configFile + "': " + str, file=sys.stderr)

def readCameraConfig(config):
    """Read single camera configuration."""
    cam = CameraConfig()

    # name
    try:
        cam.name = config["name"]
    except KeyError:
        parseError("could not read camera name")
        return False

    # path
    try:
        cam.path = config["path"]
    except KeyError:
        parseError("camera '{}': could not read path".format(cam.name))
        return False

    # stream properties
    cam.streamConfig = config.get("stream")

    cam.config = config

    cameraConfigs.append(cam)
    return True

def readConfig():
    """Read configuration file."""
    global team
    global server

    # parse file
    try:
        with open(configFile, "rt", encoding="utf-8") as f:
            j = json.load(f)
    except OSError as err:
        print("could not open '{}': {}".format(configFile, err), file=sys.stderr)
        return False

    # top level must be an object
    if not isinstance(j, dict):
        parseError("must be JSON object")
        return False

    # team number
    try:
        team = j["team"]
    except KeyError:
        parseError("could not read team number")
        return False

    # ntmode (optional)
    if "ntmode" in j:
        str = j["ntmode"]
        if str.lower() == "client":
            server = False
        elif str.lower() == "server":
            server = True
        else:
            parseError("could not understand ntmode value '{}'".format(str))

    # cameras
    try:
        cameras = j["cameras"]
    except KeyError:
        parseError("could not read cameras")
        return False
    for camera in cameras:
        if not readCameraConfig(camera):
            return False

    return True

def startCamera(config):
    """Start running the camera."""
    print("Starting camera '{}' on {}".format(config.name, config.path))
    camera = UsbCamera(config.name, config.path)
    server = CameraServer.startAutomaticCapture(camera=camera)

    camera.setConfigJson(json.dumps(config.config))
    camera.setConnectionStrategy(VideoSource.ConnectionStrategy.kConnectionKeepOpen)

    if config.streamConfig is not None:
        server.setConfigJson(json.dumps(config.streamConfig))

    return camera

####################################################################################

class Tag():

    def __init__(self, tag_size, family):
        self.family = family
        self.size = tag_size
        self.locations = {}
        self.orientations = {}
        self.found_tags = []
        self.tag_last_update = {}
        self.total_frames = 0
        corr = np.eye(3)
        corr[0, 0] = -1
        self.tag_corr = corr
    def addTag(self,id,x,y,z,theta_x,theta_y,theta_z):
        self.locations[id]=self.inchesToTranslationVector(x,y,z)
        self.orientations[id]=self.eulerAnglesToRotationMatrix(theta_x,theta_y,theta_z)
    # Calculates Rotation Matrix given euler angles.
    def eulerAnglesToRotationMatrix(self, theta_x,theta_y,theta_z):
        R_x = np.array([[1, 0, 0],
                        [0, np.cos(theta_x), -np.sin(theta_x)],
                        [0, np.sin(theta_x), np.cos(theta_x)]
                        ])

        R_y = np.array([[np.cos(theta_y), 0, np.sin(theta_y)],
                        [0, 1, 0],
                        [-np.sin(theta_y), 0, np.cos(theta_y)]
                        ])

        R_z = np.array([[np.cos(theta_z), -np.sin(theta_z), 0],
                        [np.sin(theta_z), np.cos(theta_z), 0],
                        [0, 0, 1]
                        ])

        R = np.matmul(R_z, np.matmul(R_y, R_x))

        return np.transpose(R)
        
    def inchesToTranslationVector(self,x,y,z):
        #inches to meters
        return np.array([[x],[y],[z]])*0.0254
    
    def addFoundTag(self, tags):
        self.total_frames += 1
        self.found_tags.clear()
        for tag in detected_tags:
            if tag.hamming < 0.5:
                # check if the tag has been seen for more than x amount of frames
                self.tag_last_update[tag.tag_id] = self.total_frames
                if self.total_frames - self.tag_last_update[tag.tag_id] < 3:
                    self.found_tags.append(tag)


    def getFilteredTags(self):
        return self.found_tags

    def estimate_tag_pose(self, tag_id, R,t):
        local = self.tag_corr @ np.transpose(R) @ t
        return np.matmul(self.orientations[tag_id], local + self.locations[tag_id])
    def get_estimated_tag_poses(self):
        estimated_tag_poses = []
        for tag in self.found_tags:
            estimated_tag_poses.append(self.estimate_tag_pose(tag.tag_id, tag.pose_R, tag.pose_t))
        return estimated_tag_poses
        
    def findClosestTag(self):
        closest_tag = {}
        for tag in self.found_tags:
            p = self.estimate_pose(tag.tag_id, tag.pose_R, tag.pose_t)
            diff = 0
            N = len(closest_tag)
            M = len(p)
            # Traverse in each row
            for i in range(N):
                # Traverse in column of that row
                for j in range(M):
                    diff += p[i][j]
            
            print(diff)

####################################################################################

# Constants

RES = (640,480)

camera_info = {}
camera_info["res"] = RES
# Camera Parameters:
# [fx, fy, cx, cy] f is focal length in pixels x and y, c is optical center in pixels x and y.
camera_info["params"] = [669.13345619, 684.76325169, 319.63326201, 243.27625621]

TAG_SIZE = 0.1524
FAMILIES = "tag16h5"
tags = Tag(TAG_SIZE, FAMILIES)

####################################################################################

# INITIALIZE VARIABLES #

# add tags:
# takes in id,x,y,z,theta_x,theta_y,theta_z

tags.addTag(1, 0., 0., 0., 0., 0., 0.)

# camera starting pos

camera_pose  = np.array([[0], [0], [0]])

####################################################################################

# Visualize input frame with detected tags

def visualize_frame(img, tags):
    color_img = img

    for tag in tags:
        # Add bounding rectangle
        for idx in range(len(tag.corners)):
            cv2.line(color_img, tuple(tag.corners[idx - 1, :].astype(int)), tuple(tag.corners[idx, :].astype(int)),
                    (0, 255, 0), thickness=3)
        # Add Tag ID text
        cv2.putText(color_img, str(tag.tag_id),
                    org=(tag.corners[0, 0].astype(int) + 10, tag.corners[0, 1].astype(int) + 10),
                    fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                    fontScale=0.8,
                    color=(0, 0, 255),
                    thickness=3)
        # Add Tag Corner
        cv2.circle(color_img, tuple(tag.corners[0].astype(int)), 2, color=(255, 0, 255), thickness=3)
    
    return color_img
####################################################################################

# Pose Estimation

def estimate_camera_pose(estimated_tags):
    
    tmp_poses= [estimated_tags]
    estimated_poses_list = []

    for pose in tmp_poses:
        # Apply filtering to smooth results
        tag_relative_camera_pose = np.linalg.inv(pose)

        # Find the camera position relative to the tag position
        world_camera_pose = np.matmul(pose, tag_relative_camera_pose)

        # Find the position of the robot from the camera position
        inv_rel_camera_pose = np.linalg.inv(camera_pose)
        robot_pose = np.matmul(world_camera_pose, inv_rel_camera_pose)
        estimated_poses_list.append(robot_pose)
    
    if not estimated_poses_list:
        # If we have no samples, report none
        return (None, estimated_poses_list)
			
    total = np.array([0.0, 0.0, 0.0])
    for pose in estimated_poses_list:
        total += np.array([pose[0][3], pose[1][3], pose[2][3]])
    average = total / len(estimated_poses_list)
    return (average)

####################################################################################

if __name__ == "__main__":

    if len(sys.argv) >= 2:
        configFile = sys.argv[1]

    # read configuration
    if not readConfig():
        sys.exit(1)

    # start NetworkTables
    ntinst = NetworkTableInstance.getDefault()
    if server:
        print("Setting up NetworkTables server")
        ntinst.startServer()
    else:
        print("Setting up NetworkTables client for team {}".format(team))
        ntinst.startClient4("wpilibpi")
        ntinst.setServerTeam(team)
        ntinst.startDSClient()

    raspberryPiTable = ntinst.getTable("RaspberryPi")

    # start cameras
    # work around wpilibsuite/allwpilib#5055
    CameraServer.setSize(CameraServer.kSize160x120)
    for config in cameraConfigs:
        cameras.append(startCamera(config))

    detector = Detector(families='tag16h5',
                        nthreads=2,
                        quad_decimate=4,
                        quad_sigma=0.5,
                        refine_edges=2,
                        decode_sharpening=0.25,
                        debug=0
                        )
    cvSink = CameraServer.getVideo()
    processedOutput = CameraServer.putVideo("processedOutput", 640, 480)

    frame = np.zeros(shape=(RES[1], RES[0], 3), dtype=np.uint8)


    # loop forever
    while True:
        time, frame = cvSink.grabFrame(frame)
        if time == 0:
            processedOutput.notifyError(cvSink.getError())

            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        detected_tags = detector.detect(gray, estimate_tag_pose=True, camera_params=camera_info["params"], tag_size=TAG_SIZE)
        for tag in detected_tags:
            tags.addFoundTag(tag)
        
        outputTags = []

        for tag in tags.getFilteredTags():
            ID = tag.tag_id
            R = tag.pose_R
            t = tag.pose_t

            yaw = math.degrees(math.atan2(-R[2, 0], math.sqrt(R[0,0]*R[0,0] + R[1,0]*R[1,0])))
            pitch =  math.degrees(math.atan2(R[1,2]/math.cos(yaw), R[2,2]/math.cos(yaw)))
            roll = math.degrees(math.atan2(R[1,0]/math.cos(yaw), R[0,0]/math.cos(yaw)))

            # convert meters to inches
            x = (int((t[0]).astype(float)*39.37*1000)) / 1000
            y = (int((t[1]).astype(float)*39.37*1000)) / 1000
            z = (int((t[2]).astype(float)*39.37*1000)) / 1000
            #pose = np.multiply(estimate_camera_pose(tags.get_estimated_tag_poses()), 39.37)
            #pose = np.multiply(tags.estimate_tag_pose(ID, R, t), 39.37)
            distances = [x, y, z]
            angles  = [yaw, pitch, roll]
            
            #raspberryPiTable.putString("Pose Average" + str(ID), str(pose))
            #outputTags.append(str([ID, distances, angles]))
            raspberryPiTable.putValue("Tag " + str(ID) + " x:", x)
            raspberryPiTable.putValue("Tag " + str(ID) + " y:", y)
            raspberryPiTable.putValue("Tag " + str(ID) + " z:", z)
            raspberryPiTable.putValue("Tag " + str(ID) + " yaw:", yaw)
            raspberryPiTable.putValue("Tag " + str(ID) + " pitch:", pitch)
            raspberryPiTable.putValue("Tag " + str(ID) + " roll:", roll)

        #tags.findClosestTag()
        processedOutput.putFrame(visualize_frame(frame, tags.getFilteredTags()))

"""

DETECTOR PARAMETERS:

PARAMETERS:
families : Tag families, separated with a space, default: tag36h11

nthreads : Number of threads, default: 1

quad_decimate : Detection of quads can be done on a lower-resolution image, 
improving speed at a cost of pose accuracy and a slight decrease in detection rate. 
Decoding the binary payload is still done at full resolution, default: 2.0

quad_sigma : What Gaussian blur should be applied to the segmented image (used for 
quad detection?) Parameter is the standard deviation in pixels. Very noisy images 
benefit from non-zero values (e.g. 0.8), default: 0.0

refine_edges : When non-zero, the edges of the each quad are adjusted to “snap to” 
strong gradients nearby. This is useful when decimation is employed, as it can increase 
the quality of the initial quad estimate substantially. Generally recommended to be 
on (1). Very computationally inexpensive. Option is ignored if quad_decimate = 1, 
default: 1

decode_sharpening : How much sharpening should be done to decoded images? This can 
help decode small tags but may or may not help in odd lighting conditions or low light 
conditions, default = 0.25

debug : If 1, will save debug images. Runs very slow, default: 0
"""
