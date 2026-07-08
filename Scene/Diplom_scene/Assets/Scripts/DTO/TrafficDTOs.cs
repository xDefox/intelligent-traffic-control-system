using System;
using System.Collections.Generic;

namespace SmartCrossroads.Network.DTO
{
    [Serializable]
    public class LaneDetectionDTO
    {
        public string lane_id;
        public int car_count;
        public float avg_speed;
    }

    [Serializable]
    public class IntersectionUpdateDTO
    {
        public string intersection_id;
        public string camera_id;
        public List<LaneDetectionDTO> lanes;
    }
}