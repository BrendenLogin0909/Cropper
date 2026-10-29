"""Local edge/contour print detection. Suggestions only; never saves or crops."""
import time
import cv2
import numpy as np
from PIL import Image


def predict_corners(image: Image.Image, target=None) -> dict:
    started = time.perf_counter()
    original_width, original_height = image.size
    preview = image.convert('RGB')
    preview.thumbnail((960, 960), Image.Resampling.BILINEAR)
    rgb = np.asarray(preview)
    height, width = rgb.shape[:2]
    if min(width, height) < 24:
        return {'candidates': [], 'elapsed_ms': 0}
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    channels = [cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), *cv2.split(lab)]
    kernels = [np.ones((3, 3), np.uint8), np.ones((7, 7), np.uint8)]
    candidates = []
    total = width * height
    real_edges = np.zeros((height,width),np.uint8)
    for channel in channels:
        real_edges |= cv2.Canny(cv2.GaussianBlur(channel,(5,5),0),12,36)
    real_distance = cv2.distanceTransform(255-real_edges,cv2.DIST_L2,3)
    masks = []
    for channel in channels:
        blurred = cv2.GaussianBlur(channel, (5, 5), 0)
        _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        masks.extend([mask, 255-mask])
    # Wood/page texture mostly changes luminance. Colour-distance segmentation
    # complements edges when a rounded print does not form a closed contour.
    border = np.concatenate([lab[:5].reshape(-1,3), lab[-5:].reshape(-1,3),
                             lab[:, :5].reshape(-1,3), lab[:, -5:].reshape(-1,3)])
    bg = np.median(border, axis=0)
    delta = lab.astype(np.float32)-bg
    colour_distance = np.sqrt(delta[:,:,0]**2*.15 + delta[:,:,1]**2 + delta[:,:,2]**2)
    for threshold in [12, 20, 30]:
        masks.append((colour_distance > threshold).astype(np.uint8)*255)
    for mask in masks:
        filled = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15,15),np.uint8))
        contours, _ = cv2.findContours(filled, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if abs(cv2.contourArea(contour)) < total*.08:
                continue
            hull = cv2.convexHull(contour)
            for epsilon in [.015,.03,.05]:
                quad=cv2.approxPolyDP(hull, epsilon*cv2.arcLength(hull,True),True)
                if len(quad)==4:
                    # Treat segmented print outlines as additional edge evidence.
                    outline=np.zeros((height,width),np.uint8)
                    cv2.drawContours(outline,[quad],-1,255,1)
                    channels.append(outline)
                    break
    for channel in channels:
        blurred = cv2.GaussianBlur(channel, (5, 5), 0)
        for low, high in [(12, 36), (30, 90), (60, 150)]:
            edges = cv2.Canny(blurred, low, high)
            distance = real_distance
            for kernel in kernels:
                connected = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
                contours, _ = cv2.findContours(connected, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    contour_area = abs(cv2.contourArea(contour))
                    if contour_area < total * .06:
                        continue
                    perimeter = cv2.arcLength(contour, True)
                    for epsilon in [.012, .025, .04, .06]:
                        quad = cv2.approxPolyDP(contour, epsilon * perimeter, True)
                        if len(quad) != 4 or not cv2.isContourConvex(quad):
                            continue
                        points = quad[:, 0, :].astype(np.float32)
                        on_frame = (points[:,0] < width*.018) | (points[:,0] > width*.982) | (points[:,1] < height*.018) | (points[:,1] > height*.982)
                        if on_frame.sum() >= 3:
                            continue
                        area = abs(cv2.contourArea(points))
                        if not total * .06 < area < total * .97:
                            continue
                        # Reject skinny page decorations and extreme perspective shapes.
                        sides = np.linalg.norm(points - np.roll(points, -1, axis=0), axis=1)
                        if sides.min() < min(width, height) * .12 or sides.max() / sides.min() > 5:
                            continue
                        angles = []
                        support = []
                        contrasts = []
                        for i, p in enumerate(points):
                            before = points[(i-1) % 4] - p
                            after = points[(i+1) % 4] - p
                            angles.append(abs(float(np.dot(before, after) / (np.linalg.norm(before)*np.linalg.norm(after)))))
                            line = np.linspace(p, points[(i+1) % 4], 100).astype(int)
                            support.append(float(np.mean(distance[line[:,1], line[:,0]] < 6)))
                            segment = points[(i+1)%4]-p
                            normal = np.array([-segment[1],segment[0]]) / np.linalg.norm(segment) * 5
                            middle = np.linspace(p,points[(i+1)%4],100)[10:90]
                            inside = np.clip(middle+normal,[0,0],[width-1,height-1]).astype(int)
                            outside = np.clip(middle-normal,[0,0],[width-1,height-1]).astype(int)
                            change = lab[inside[:,1],inside[:,0]].astype(float)-lab[outside[:,1],outside[:,0]].astype(float)
                            contrasts.append(float(np.mean(np.linalg.norm(change,axis=1)>15)))
                        if max(angles) > .75 or min(support) < .55:
                            continue
                        if min(contrasts) < .35:
                            continue
                        # A contour surrounding the print should fit the quadrilateral.
                        if min(area, contour_area) / max(area, contour_area) < .8:
                            continue
                        centre = points.mean(axis=0)
                        centrality = 1 - min(1, np.linalg.norm(centre - [width/2,height/2]) / (max(width,height)/2))
                        score = .45 * (area / total) ** .5 + .25 * np.mean(support) + .2 * np.mean(contrasts) + .1 * centrality - .15 * np.mean(angles)
                        if target is not None:
                            scaled = (float(target[0])*width/original_width, float(target[1])*height/original_height)
                            if cv2.pointPolygonTest(points, scaled, False) < 0:
                                continue
                        # Consistent TL, TR, BR, BL order and deduplicate channel/threshold variants.
                        points = points[np.argsort(np.arctan2(points[:,1]-centre[1],points[:,0]-centre[0]))]
                        points = np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)
                        if any(np.mean(np.linalg.norm(points-c['raw'],axis=1)) < max(width,height)*.015 for c in candidates):
                            continue
                        candidates.append({'raw':points, 'score':float(score)})
                        break
    candidates.sort(key=lambda c:c['score'], reverse=True)
    return {'candidates': [
        {'points':(c['raw'] * [original_width/width,original_height/height]).tolist(), 'score':round(c['score'],3)}
        for c in candidates[:8]
    ], 'elapsed_ms':round((time.perf_counter()-started)*1000)}
